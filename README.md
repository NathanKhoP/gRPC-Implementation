# gRPC Chat Application

A simple chat application demonstrating the use of gRPC in Python for bidirectional streaming communication.

## Project Structure

```
├── client.py             # Terminal-based chat client implementation
├── server.py             # Chat server implementation
└── proto/
    ├── chat.proto        # Protocol Buffers definition
    ├── chat_pb2.py       # Generated Protocol Buffers classes
    └── chat_pb2_grpc.py  # Generated gRPC service classes
```

## Requirements

- Python 3.6+
- gRPC Python libraries

## Installation

Install the required Python packages:

```bash
pip install grpcio grpcio-tools
```

## Usage

### Starting the Server

First, run the gRPC server:

```bash
python server.py
```

The server will start listening on port 50051.

### Terminal-based Client

Run the terminal-based client:

```bash
python client.py
```

You can run multiple client instances in separate terminals to see the bidirectional chat in action.

When running multiple clients:
1. Messages sent from one client will appear only on other clients' terminals
2. The server handles broadcasting messages to all connected clients except the sender
3. You can use `/history <number>` to see the chat history
4. You can use `/batch` to use client-streaming mode.

## Features

- Real-time messaging between multiple clients using bidirectional gRPC streams
- Messages appear only on other users' terminals (true bidirectional streaming)
- User join/leave notifications
- Chat history retrieval with customizable message limits
- Batch messaging mode for sending multiple messages at once
- System messages for user events (joining, leaving)

## Protocol Buffers

The communication protocol is defined in `proto/chat.proto`. If you make changes to this file, regenerate the Python code using:

```bash
python -m grpc_tools.protoc -I proto --python_out=proto --grpc_python_out=proto proto/chat.proto
```

Note: The generated files are now stored in the `proto` directory for better organization.

## Architecture

This application demonstrates how to implement a chat service using gRPC:

- **gRPC Server**: Handles the core chat functionality, user management, and message distribution
- **Terminal Client**: Direct gRPC client for command-line usage with bidirectional streaming