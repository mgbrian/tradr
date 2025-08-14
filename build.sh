#!/bin/bash

# Compile all protos in the current folder to Python, then generate JS stubs
# and bundle a browser client for grpc-web.

set -euo pipefail

#  Paths & env

SCRIPT_PATH=$(readlink -f "${BASH_SOURCE[0]}")
SCRIPT_DIR=$(dirname "$SCRIPT_PATH")
VENV_DIR="$SCRIPT_DIR/.requirements"

WEB_DIR="$SCRIPT_DIR/web"
WEB_SRC_PROTOS_DIR="$WEB_DIR/src/protos"
WEB_STATIC_JS_DIR="$WEB_DIR/static/js"

GLOBAL_PYTHON_EXECUTABLE=python3   # python or python3
VENV_PYTHON_EXECUTABLE=python      # python or python3

PROTO_FILES=$(find "$SCRIPT_DIR" -maxdepth 1 -name "*.proto") # not recursive

#  Helpers

die() { echo "Error: $*" >&2; exit 1; }

#  Pre-flight

if [ -z "$PROTO_FILES" ]; then
  echo "No .proto files found. Exiting."
  exit 0
fi

#  Python venv setup and proto compilation to Python

if [ ! -d "$VENV_DIR" ]; then
  echo "Virtual environment not found. Creating one at '$VENV_DIR'..."
  $GLOBAL_PYTHON_EXECUTABLE -m venv "$VENV_DIR" || die "venv creation failed"
  echo "Virtual environment created. Activating it and installing requirements."
fi

# shellcheck disable=SC1090
source "$VENV_DIR/bin/activate" || die "activating virtualenv failed"

if [ -f "$SCRIPT_DIR/requirements.txt" ]; then
  pip install -r "$SCRIPT_DIR/requirements.txt" || die "pip install failed"
fi

echo "Compiling the following proto files to Python:"
echo "$PROTO_FILES"
echo ""

echo "Compiling to Python..."
$VENV_PYTHON_EXECUTABLE -m grpc_tools.protoc \
  -I"$SCRIPT_DIR" \
  --python_out="$SCRIPT_DIR" \
  --pyi_out="$SCRIPT_DIR" \
  --grpc_python_out="$SCRIPT_DIR" \
  $PROTO_FILES \
  || die "Proto compilation to Python failed."
echo "Python compilation done."

#  Proto compilation to JS (gRPC Web)

echo ""
echo "Preparing web directories..."
mkdir -p "$WEB_SRC_PROTOS_DIR" "$WEB_STATIC_JS_DIR"

echo "Compiling to JS (grpc-web stubs)..."
# NOTE: Only compiling service.proto to web. Adjust to compile more protos in the future if needed!
protoc -I "$SCRIPT_DIR" "$SCRIPT_DIR/service.proto" \
  --js_out=import_style=commonjs:"$WEB_SRC_PROTOS_DIR" \
  --grpc-web_out=import_style=commonjs,mode=grpcwebtext:"$WEB_SRC_PROTOS_DIR" \
  || { echo "Proto compilation to JS failed. For help, see https://github.com/grpc/grpc-web"; exit 1; }
echo "JS compilation done."

#  Bundle browser client

echo ""
echo "Setting up npm deps for web bundling..."
pushd "$WEB_DIR" >/dev/null

# Initialize package.json if missing
if [ ! -f package.json ]; then
  npm init -y >/dev/null
fi

# Install deps if missing
need_install=0
[ ! -d node_modules/grpc-web ] && need_install=1
[ ! -d node_modules/google-protobuf ] && need_install=1
[ ! -d node_modules/esbuild ] && need_install=1

if [ $need_install -eq 1 ]; then
  echo "Installing npm dependencies (grpc-web, google-protobuf, esbuild)..."
  npm i grpc-web google-protobuf esbuild || die "npm install failed"
else
  echo "npm dependencies already present."
fi

# Bundle client_web.js to static/js/bundle.js
echo "Bundling web/src/client_web.js -> web/static/js/bundle.js ..."
npx esbuild "src/client_web.js" \
  --bundle \
  --platform=browser \
  --format=iife \
  --outfile="static/js/bundle.js" \
  || die "esbuild bundling failed"

popd >/dev/null

echo ""
echo "Build completed successfully."
