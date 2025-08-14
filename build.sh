#!/bin/bash

# Compile all protos in the current folder to Python.

SCRIPT_PATH=$(readlink -f "${BASH_SOURCE[0]}")
SCRIPT_DIR=$(dirname "$SCRIPT_PATH")
VENV_DIR=$SCRIPT_DIR/.requirements
GLOBAL_PYTHON_EXECUTABLE=python3  # python or python3
VENV_PYTHON_EXECUTABLE=python  # python or python3
PROTO_FILES=$(find "$SCRIPT_DIR" -name "*.proto" -maxdepth 1) # maxdepth 1 -> not recursive

if [ -z "$PROTO_FILES" ]; then
  echo "No .proto files found. Exiting."
  exit 0
fi

if [ ! -d "$VENV_DIR" ]; then
    echo "Virtual environment not found. Creating one at '$VENV_DIR'..."

    $GLOBAL_PYTHON_EXECUTABLE -m venv "$VENV_DIR"
    echo "Virtual environment created. Activating it and installing requirements."

    source $VENV_DIR/bin/activate || { echo "Error activating virtualenv."; exit 1; }

    pip install -r requirements.txt || { echo "Error installing requirements."; exit 1; }

else
    source $VENV_DIR/bin/activate || { echo "Error activating virtualenv."; exit 1; }
fi

# source $VENV_DIR/bin/activate || { echo "Error activating virtualenv."; exit 1; }

echo "Compiling the following proto files:"
echo "$PROTO_FILES"

echo ""
echo "Compiling to Python..."
$VENV_PYTHON_EXECUTABLE -m grpc_tools.protoc \
    -I$SCRIPT_DIR \
    --python_out=$SCRIPT_DIR \
    --pyi_out=$SCRIPT_DIR \
    --grpc_python_out=$SCRIPT_DIR \
    $PROTO_FILES \
    || { echo "Proto compilation to Python failed."; exit 1; }

echo "Python compilation done."

echo ""
echo "Compiling to JS..."
protoc -I . service.proto \
 --js_out=import_style=commonjs:./web/src/protos \
 --grpc-web_out=import_style=commonjs,mode=grpcwebtext:./web/src/protos \
 || { echo "Proto compilation to JS failed. For help, see https://github.com/grpc/grpc-web"; exit 1; }

echo "JS compilation done."
