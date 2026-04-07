# Defining curstom ollama models 

## Modelfiles

Create a new model config using a modelfile:
```bash
ollama create gemma4-128k -f Modelfile
```

## Environment

```bash
sudo systemctl edit ollama.service
```
Enable flash attention.

Enable KV cache quantization to save some memory. **q8_0** should not cause many problems. 
Try **q4_0** to save more memory. Note that q4 is not a good idea for coding tasks and long contexts.

Add the environment:

```ini
[Service]
Environment="OLLAMA_KV_CACHE_TYPE=q8_0"
Environment="OLLAMA_FLASH_ATTENTION=1"
```

Restart the service:

```bash
sudo systemctl daemon-reload
sudo systemctl restart ollama
```

