#!/bin/bash

IMAGE_NAME="news-reader-app"
CONTAINER_NAME="news-reader-container"
PORT=8503

# 1. Build the image
echo "Building Docker image..."
docker build -t $IMAGE_NAME .

# 2. Stop/Remove existing container
if [ "$(docker ps -aq -f name=$CONTAINER_NAME)" ]; then
    echo "Stopping existing container..."
    docker stop $CONTAINER_NAME
    docker rm $CONTAINER_NAME
fi

# 3. Run container with SSH key mount
# Mounting host's ~/.ssh to /root/.ssh inside container (assuming container runs as root)
echo "Starting container on port $PORT..."
docker run -d \
  --network host \
  -v $HOME/.ssh:/root/.ssh \
  -v $(pwd):/app \
  --name $CONTAINER_NAME \
  $IMAGE_NAME

echo "News Reader is running at http://localhost:$PORT"
echo "Logs:"
docker logs -f $CONTAINER_NAME
