#!/bin/sh
docker run --rm -it --name ai-toolbox-cockpit-halogen-server-334446c6 \
  --pull=always \
  --device /dev/kfd \
  --device /dev/dri \
  --group-add video \
  --group-add $(getent group render | cut -d: -f3) \
  --security-opt seccomp=unconfined \
  --ipc=host \
  --ulimit memlock=-1:-1 \
  -p 0.0.0.0:8731:8731 \
  --security-opt no-new-privileges \
  -v /run/media/briggen/DEV/.aitoolbox/models/qwen38-flash-next-w4b.hgn:/models/qwen38-flash-next-w4b.hgn:ro \
  -v /run/media/briggen/DEV/.aitoolbox/models/qwen38-flash-next-w4b.overlay-speed.hgn:/models/qwen38-flash-next-w4b.overlay-speed.hgn:ro \
  -v /run/media/briggen/DEV/.aitoolbox/models/tokenizer/chat_template.jinja:/models/tokenizer/chat_template.jinja:ro \
  -v /run/media/briggen/DEV/.aitoolbox/models/tokenizer/generation_config.json:/models/tokenizer/generation_config.json:ro \
  -v /run/media/briggen/DEV/.aitoolbox/models/tokenizer/merges.txt:/models/tokenizer/merges.txt:ro \
  -v /run/media/briggen/DEV/.aitoolbox/models/tokenizer/tokenizer.json:/models/tokenizer/tokenizer.json:ro \
  -v /run/media/briggen/DEV/.aitoolbox/models/tokenizer/tokenizer_config.json:/models/tokenizer/tokenizer_config.json:ro \
  -v /run/media/briggen/DEV/.aitoolbox/models/tokenizer/vocab.json:/models/tokenizer/vocab.json:ro \
  -v /run/media/briggen/DEV/.aitoolbox/models/qwen38-flash-next-vision.hgn:/models/qwen38-flash-next-vision.hgn:ro \
  -e HALOGEN_CHECKPOINT=/models/qwen38-flash-next-w4b.hgn \
  -e HALOGEN_CK_OVERLAY=/models/qwen38-flash-next-w4b.overlay-speed.hgn \
  -e HALOGEN_TOKENIZER=/models/tokenizer \
  -e HALOGEN_API_PORT=8731 \
  -e HALOGEN_CTX=262144 \
  -e HALOGEN_KV_POOL_POSITIONS=524288 \
  -e HALOGEN_KV_SLOTS=4 \
  -e HALOGEN_PROMPT_CACHE=2\
  -e HALOGEN_VISION_TOWER=/models/qwen38-flash-next-vision.hgn \
  ghcr.io/peonist-ai/halogen-flash-server:latest

