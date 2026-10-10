# MCP server installation root

Install each stdio MCP server in its own directory named after its Strixper
server ID:

```text
mcp-servers/
  <server-id>/
    package.json
    node_modules/
```

For Node.js servers, install a pinned package into that directory with:

```bash
npm install --prefix "mcp-servers/<server-id>" "<package>@<version>"
```

Configure the Strixper MCP command to match the installed executable. The
backend prepends `mcp-servers/<server-id>/bin`,
`mcp-servers/<server-id>/.venv/bin`,
`mcp-servers/<server-id>/node_modules/.bin`, and the shared
`mcp-servers/bin` directory to that process's `PATH`. MCP configuration and
environment secret references remain in Strixper's protected MCP manager, not
in package manifests.

Docker installs bundled packages into `/mcp-servers` in the image and mounts
that root from the persistent `strixper-mcp-servers` volume. To install another
package for Docker-managed Strixper, use its matching root inside the
container:

```bash
docker exec strixper npm install --prefix "/mcp-servers/<server-id>" "<package>@<version>"
```

Only install and enable server packages you trust: MCP processes run with the
backend container's permissions and mounts.
