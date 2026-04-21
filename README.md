# chemsafe-agent
AI Agent for Chemical Safety

---

## Quick Start

Follow these steps to run the application locally using Docker:

### 1. Build the Docker image

```bash
docker build --platform linux/amd64 -t chemsafeagent:trial .
```

### 2. Run the container

```bash
docker run --rm -it -p 7860:7860 chemsafeagent:trial
```

### 3. Open in browser

Navigate to:

```
http://localhost:7860
```
