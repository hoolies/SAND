#!/bin/bash

set -e

# Check if docker exists
command -v docker &> /dev/null || { echo "Install docker first"; exit 1; }

image_build() {
  IMAGE_NAME=$1
  DOCKERFILE=$2

  echo "Building image. IMAGE=$IMAGE_NAME"
  docker build . \
    --file $DOCKERFILE \
    -t "$IMAGE_NAME"
}

image_build IMAGE_NAME Dockerfile

