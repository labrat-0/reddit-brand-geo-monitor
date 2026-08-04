FROM apify/actor-python:3.12

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# The vendored reddit-scraper engine drives a real Chromium to solve Reddit's
# network-security challenge, so the browser must be installed in the image.
RUN playwright install chromium --with-deps

# Pre-download the embedding model into the image so the first run does not pay
# the download latency (semantic relevance gate).
RUN python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5')"

COPY . ./

CMD ["python", "-m", "src"]
