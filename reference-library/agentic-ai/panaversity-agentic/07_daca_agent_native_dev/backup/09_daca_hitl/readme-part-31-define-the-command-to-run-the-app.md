# Define the command to run the app
CMD ["uv", "run", "streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0"]
```

#### Explanation of the Dockerfile
- `FROM python:3.9-slim`: Uses a lightweight Python 3.9 image.
- `RUN pip install uv`: Installs `uv` in the image.
- `WORKDIR /app`: Sets the working directory to `/app`.
- `COPY pyproject.toml uv.lock ./`: Copies the dependency files.
- `RUN uv sync --frozen`: Installs the dependencies using `uv`, respecting the locked versions in `uv.lock`.
- `COPY . .`: Copies the application code (`app.py`).
- `EXPOSE 8501`: Streamlit runs on port `8501` by default.
- `CMD ["uv", "run", "streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0"]`: Runs the Streamlit app using `uv run` on port `8501`.

---