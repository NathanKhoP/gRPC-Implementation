from concurrent import futures
import grpc
import uuid
import datetime
import threading
import sys
import os
import time
import queue

sys.path.append(os.path.join(os.path.dirname(__file__), 'proto'))
from chat_pb2 import ChatMessage, User, HistoryRequest
from chat_pb2_grpc import ChatServiceStub


def get_user_input(prompt):
    """Get user input with a prompt"""
    try:
        return input(prompt)
    except EOFError:
        return None


def generate_chat_message(user_id, username, message):
    """Create a new chat message"""
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return ChatMessage(
        user_id=user_id,
        username=username,
        message=message,
        timestamp=now
    )


def run():
    """Run the chat client"""
    # Connect to the server
    server_address = 'localhost:50051'
    channel = grpc.insecure_channel(server_address)
    stub = ChatServiceStub(channel)
    
    print("=== Welcome to gRPC Chat ===")
    print(f"Connecting to {server_address}...")
    
    # Get user information
    username = get_user_input("Enter your username: ")
    if not username:
        username = f"User-{uuid.uuid4().hex[:6]}"
        print(f"Username not provided, using: {username}")
    
    user_id = str(uuid.uuid4())
    
    try:
        # Join the chat
        join_response = stub.JoinChat(User(user_id=user_id, username=username))
        print(f"Server: {join_response.message}")
        
        print("Chat commands:")
        print("- Type your message and press Enter to send")
        print("- Type '/history <number>' to get chat history (e.g. '/history 10')")
        print("- Type '/batch' to start batch messaging mode")
        print("- Type '/ping' to check connection latency to the server")
        print("- Type 'exit' to leave the chat")
        print("-" * 40)
        
        # Get initial chat history using server streaming RPC
        get_initial_history(stub, user_id, username)
        
        # Create a shared queue for outgoing messages
        message_queue = queue.Queue()
        # Create an event to signal when to exit
        exit_event = threading.Event()

        # Start the bidirectional stream first
        stream_event = threading.Event()
        stream_thread = threading.Thread(
            target=handle_chat_stream,
            args=(stub, user_id, username, message_queue, exit_event, stream_event)
        )
        stream_thread.daemon = True
        stream_thread.start()
        
        # Wait for stream to be established
        while not stream_event.is_set() and not exit_event.is_set():
            time.sleep(0.1)
        
        # Now handle user input
        try:
            while not exit_event.is_set():
                message = get_user_input(f"[ {username} ] >>> ")
                if message is None or message.lower() == 'exit':
                    exit_event.set()
                    break
                
                # Handle special commands
                if message.startswith('/history'):
                    try:
                        # Parse the limit from the command
                        parts = message.split()
                        limit = 10  # default
                        if len(parts) > 1:
                            limit = int(parts[1])
                        
                        # Call the history command function
                        get_chat_history(stub, user_id, limit)
                        continue
                    except ValueError:
                        print("Invalid number. Usage: /history <number>")
                        continue
                
                if message == '/batch':
                    print("Batch messaging mode. Enter multiple messages, type '/end' on a new line to send all messages.")
                    batch_messages = []
                    while True:
                        batch_line = get_user_input("batch> ")
                        if batch_line == '/end':
                            break
                        batch_messages.append(batch_line)
                    
                    if batch_messages:
                        send_batch_messages(stub, user_id, username, batch_messages)
                    continue
                
                if message == '/ping':
                    ping_server(stub, user_id, username)
                    continue
                
                # Add message to the queue for the stream to send
                message_queue.put(message)
                
        except (KeyboardInterrupt, EOFError):
            exit_event.set()
        
        # Wait for the exit event
        while not exit_event.is_set():
            time.sleep(0.1)
        
    except grpc.RpcError as e:
        print(f"Error: {e.details() if hasattr(e, 'details') else str(e)}")
    except KeyboardInterrupt:
        print("\nExiting chat application...")
    finally:
        # Wait a moment before closing the channel to allow final messages
        time.sleep(0.5)
        channel.close()


def handle_chat_stream(stub, user_id, username, message_queue, exit_event, stream_event):
    """Handle bidirectional stream for sending and receiving messages"""
    try:
        # Function to generate messages for the stream
        def message_generator():
            # Send an initial message to establish the stream
            yield ChatMessage(
                user_id=user_id,
                username=username,
                message="",
                timestamp=""
            )
            
            # Signal that the stream is established
            stream_event.set()
            
            # Continue sending messages from the queue
            while not exit_event.is_set():
                try:
                    # Try to get a message from the queue (non-blocking)
                    message = message_queue.get(block=True, timeout=0.5)
                    
                    if message:
                        # Create and yield the message
                        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        chat_msg = ChatMessage(
                            user_id=user_id,
                            username=username,
                            message=message,
                            timestamp=now
                        )
                        yield chat_msg
                except queue.Empty:
                    # No message to send, just continue
                    continue
                except Exception as e:
                    print(f"\rError in message generator: {e}")
                    if exit_event.is_set():
                        break
        
        # Set up metadata
        metadata = [('user_id', user_id)]
        
        # Start the bidirectional stream
        responses = stub.ChatStream(message_generator(), metadata=metadata)
        
        # Process responses from the server
        for response in responses:
            # Skip empty messages or our own messages
            if not response.message or response.user_id == user_id:
                continue
            
            # Display messages from other users (clear line, print message, restore prompt)
            print(f"\r[{response.timestamp}] {response.username}: {response.message}")
            print(f"[ {username} ] >>> ", end='', flush=True)
            
    except grpc.RpcError as e:
        print(f"\rStream error: {e.details() if hasattr(e, 'details') else str(e)}")
        exit_event.set()
    except Exception as e:
        print(f"\rError in chat stream: {e}")
        exit_event.set()
    finally:
        # Make sure stream_event is set so main thread doesn't hang
        stream_event.set()


def get_initial_history(stub, user_id, username, limit=5):
    """Get initial chat history using server streaming RPC"""
    print(f"Fetching last {limit} messages...")
    try:
        # Create a request for chat history
        history_request = HistoryRequest(
            user_id=user_id,
            message_limit=limit,
            include_system_messages=True
        )
        
        # Call the server streaming RPC
        responses = stub.GetChatHistory(history_request)
        
        # Process and display the history
        messages = []
        for message in responses:
            messages.append(message)
        
        # Print messages in chronological order
        if messages:
            print(f"\nChat History (last {len(messages)} messages):")
            for message in reversed(messages):
                print(f"[{message.timestamp}] {message.username}: {message.message}")
            print("-" * 40)
        else:
            print("No chat history yet.")
            
    except grpc.RpcError as e:
        print(f"Error getting history: {e.details() if hasattr(e, 'details') else str(e)}")


def get_chat_history(stub, user_id, limit=10):
    """Get chat history using server streaming RPC"""
    print(f"Fetching last {limit} messages...")
    try:
        # Create a request for chat history
        history_request = HistoryRequest(
            user_id=user_id,
            message_limit=limit,
            include_system_messages=True
        )
        
        # Call the server streaming RPC
        responses = stub.GetChatHistory(history_request)
        
        # Process and display the history
        messages = []
        for message in responses:
            messages.append(message)
        
        # Print messages in chronological order
        if messages:
            print(f"\nChat History (last {len(messages)} messages):")
            for message in reversed(messages):
                print(f"[{message.timestamp}] {message.username}: {message.message}")
            print("-" * 40)
        else:
            print("No chat history available.")
            
    except grpc.RpcError as e:
        print(f"Error getting history: {e.details() if hasattr(e, 'details') else str(e)}")


def send_batch_messages(stub, user_id, username, messages):
    """Send multiple messages at once using client streaming RPC"""
    if not messages:
        return
    
    print(f"Sending {len(messages)} messages in batch mode...")
    
    try:
        # Create a generator for messages
        def message_generator():
            for msg in messages:
                if msg.strip():  # Skip empty messages
                    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    yield ChatMessage(
                        user_id=user_id,
                        username=username,
                        message=msg,
                        timestamp=now
                    )
        
        # Set up metadata
        metadata = [('user_id', user_id)]
        
        # Call the client streaming RPC
        response = stub.SendMessages(message_generator(), metadata=metadata)
        
        # Display the response
        print(f"Batch message result: {response.summary}")
        
    except grpc.RpcError as e:
        print(f"Error sending batch messages: {e.details() if hasattr(e, 'details') else str(e)}")


def ping_server(stub, user_id, username):
    """Send a ping to the server and measure response time"""
    try:
        print("Pinging server...")
        start_time = time.time()
        
        # Create a ping message with a timestamp
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ping_message = ChatMessage(
            user_id=user_id,
            username=username,
            message="PING",
            timestamp=now
        )
        
        # Send ping through JoinChat since it's a simple RPC call
        response = stub.JoinChat(User(user_id=user_id, username=username))
        
        # Calculate roundtrip time
        end_time = time.time()
        roundtrip_ms = (end_time - start_time) * 1000  # Convert to milliseconds
        
        print(f"Server responded in {roundtrip_ms:.2f} ms")
        print(f"Server message: {response.message}")
        
    except grpc.RpcError as e:
        print(f"Error pinging server: {e.details() if hasattr(e, 'details') else str(e)}")


if __name__ == '__main__':
    run()