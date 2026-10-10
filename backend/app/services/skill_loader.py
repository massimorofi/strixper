"""Safe discovery and progressive loading of Agent Skills folders."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from agents import FunctionTool

MAX_SKILL_FILE_BYTES = 100_000
MAX_RESOURCE_BYTES = 64_000
MAX_FRONTMATTER_BYTES = 8_192
_SKILL_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_ALLOWED_METADATA = {
    "name",
    "description",
    "license",
    "compatibility",
    "metadata",
    "allowed-tools",
}


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    directory: Path
    instructions: str


@dataclass(frozen=True)
class SkillScan:
    skills: dict[str, Skill]
    entries: list[dict[str, Any]]


def _read_skill_file(path: Path) -> str:
    size = path.stat().st_size
    if size > MAX_SKILL_FILE_BYTES:
        raise ValueError(f"file exceeds {MAX_SKILL_FILE_BYTES} bytes")
    return path.read_text(encoding="utf-8")


def scan_skills(skills_dir: str | Path) -> SkillScan:
    """Scan direct child folders without importing or executing their files."""
    configured_root = Path(skills_dir).expanduser()
    configured_root.mkdir(parents=True, exist_ok=True)
    root = configured_root.resolve(strict=True)
    skills: dict[str, Skill] = {}
    entries: list[dict[str, Any]] = []
    candidates: list[tuple[Path, Skill]] = []

    for directory in sorted(root.iterdir(), key=lambda item: item.name.casefold()):
        try:
            if directory.resolve(strict=True).parent != root:
                continue
            if not directory.is_dir():
                continue
        except (OSError, RuntimeError):
            continue
        try:
            resolved_dir = directory.resolve(strict=True)
            if not resolved_dir.is_relative_to(root):
                raise ValueError("skill directory resolves outside SKILLS_DIR")
            if not _SKILL_NAME.fullmatch(directory.name) or len(directory.name) > 64:
                raise ValueError("directory name must be a lowercase hyphenated skill name")
            manifest = directory / "SKILL.md"
            resolved_manifest = manifest.resolve(strict=True)
            if not resolved_manifest.is_relative_to(resolved_dir):
                raise ValueError("SKILL.md resolves outside the skill directory")
            if not resolved_manifest.is_file():
                raise ValueError("SKILL.md is not a regular file")
            source = _read_skill_file(resolved_manifest)
            if not source.startswith("---\n"):
                raise ValueError("SKILL.md must start with YAML frontmatter")
            closing = source.find("\n---\n", 4)
            if closing < 0:
                raise ValueError("YAML frontmatter is not terminated")
            if closing > MAX_FRONTMATTER_BYTES:
                raise ValueError("YAML frontmatter is too large")
            frontmatter = yaml.safe_load(source[4:closing])
            if not isinstance(frontmatter, dict):
                raise ValueError("YAML frontmatter must be a mapping")
            unknown = set(frontmatter) - _ALLOWED_METADATA
            if unknown:
                raise ValueError(f"unsupported metadata fields: {', '.join(sorted(map(str, unknown)))}")
            for key in ("license", "compatibility"):
                if key in frontmatter and not isinstance(frontmatter[key], str):
                    raise ValueError(f"frontmatter {key} must be a string")
            if "metadata" in frontmatter and not isinstance(frontmatter["metadata"], dict):
                raise ValueError("frontmatter metadata must be a mapping")
            if "allowed-tools" in frontmatter and not isinstance(
                frontmatter["allowed-tools"], str
            ):
                raise ValueError("frontmatter allowed-tools must be a string")
            name = frontmatter.get("name")
            description = frontmatter.get("description")
            if not isinstance(name, str) or not _SKILL_NAME.fullmatch(name):
                raise ValueError("frontmatter name must be lowercase and hyphenated")
            if name != directory.name:
                raise ValueError("frontmatter name must match the directory name")
            if not isinstance(description, str) or not description.strip():
                raise ValueError("frontmatter description is required")
            if len(description) > 1024:
                raise ValueError("description exceeds 1024 characters")
            instructions = source[closing + 5 :].strip()
            skill = Skill(name, description.strip(), resolved_dir, instructions)
            candidates.append((directory, skill))
        except (OSError, UnicodeError, ValueError, yaml.YAMLError) as exc:
            entries.append(
                {
                    "directory": directory.name,
                    "valid": False,
                    "error": str(exc),
                }
            )

    counts: dict[str, int] = {}
    for _, skill in candidates:
        counts[skill.name] = counts.get(skill.name, 0) + 1
    for directory, skill in candidates:
        if counts[skill.name] > 1:
            entries.append(
                {
                    "directory": directory.name,
                    "name": skill.name,
                    "valid": False,
                    "error": "duplicate skill name",
                }
            )
            continue
        skills[skill.name] = skill
        entries.append(
            {
                "directory": directory.name,
                "name": skill.name,
                "description": skill.description,
                "valid": True,
            }
        )
    entries.sort(key=lambda item: str(item.get("directory", "")).casefold())
    return SkillScan(skills, entries)


def _resolve_resource(skill: Skill, relative_path: str) -> Path:
    if not relative_path or Path(relative_path).is_absolute():
        raise ValueError("resource path must be relative to the skill directory")
    resource = (skill.directory / relative_path).resolve(strict=True)
    if not resource.is_relative_to(skill.directory) or not resource.is_file():
        raise ValueError("resource path must resolve to a file inside the skill directory")
    if resource.stat().st_size > MAX_RESOURCE_BYTES:
        raise ValueError(f"resource exceeds {MAX_RESOURCE_BYTES} bytes")
    return resource


def build_skill_tools(scan: SkillScan) -> list[FunctionTool]:
    """Build bounded built-in tools for listing and loading skill content."""
    descriptions = [
        {"name": skill.name, "description": skill.description}
        for skill in scan.skills.values()
    ]

    async def list_skills(_context: Any, _input: str) -> str:
        _ = (_context, _input)
        return json.dumps(descriptions, ensure_ascii=False)

    async def load_skill(_context: Any, raw_input: str) -> str:
        _ = _context
        args = json.loads(raw_input)
        name = args.get("name")
        skill = scan.skills.get(name) if isinstance(name, str) else None
        if skill is None:
            raise ValueError("unknown skill name")
        return json.dumps(
            {"name": skill.name, "instructions": skill.instructions},
            ensure_ascii=False,
        )

    async def read_skill_resource(_context: Any, raw_input: str) -> str:
        _ = _context
        args = json.loads(raw_input)
        name = args.get("name")
        relative_path = args.get("relative_path")
        skill = scan.skills.get(name) if isinstance(name, str) else None
        if skill is None:
            raise ValueError("unknown skill name")
        if not isinstance(relative_path, str):
            raise ValueError("relative_path must be a string")
        resource = _resolve_resource(skill, relative_path)
        return _read_skill_file(resource)

    return [
        FunctionTool(
            name="list_skills",
            description="List available skills and their descriptions.",
            params_json_schema={"type": "object", "properties": {}, "required": [], "additionalProperties": False},
            on_invoke_tool=list_skills,
        ),
        FunctionTool(
            name="load_skill",
            description="Load the full instructions for an installed skill.",
            params_json_schema={
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
                "additionalProperties": False,
            },
            on_invoke_tool=load_skill,
        ),
        FunctionTool(
            name="read_skill_resource",
            description="Read a bounded reference file inside an installed skill folder.",
            params_json_schema={
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "relative_path": {"type": "string"},
                },
                "required": ["name", "relative_path"],
                "additionalProperties": False,
            },
            on_invoke_tool=read_skill_resource,
        ),
    ]
