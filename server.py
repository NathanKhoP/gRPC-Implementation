import grpc
import time
import datetime
import uuid
import threading
from concurrent import futures
import sys
import os

sys.path.append(os.path.join(os.path.dirname(__file__), 'proto'))
from chat_pb2 import ChatMessage, JoinResponse, MessageResponse
from chat_pb2_grpc import ChatServiceServicer, add_ChatServiceServicer_to_server

# Dictionary to store connected users and their message queues
connected_users = {}
# Lock for thread-safe access to the connected_users dictionary
user_lock = threading.Lock()
# Store message history
message_history = []
# Lock for thread-safe access to the message history
history_lock = threading.Lock()

class ChatServicer(ChatServiceServicer):
    def __init__(self):
        # Dictionary to store usernames mapped to user_ids
        self.usernames = {}
    
    def JoinChat(self, request, context):
        """Handle a new user joining the chat"""
        user_id = request.user_id if request.user_id else str(uuid.uuid4())
        username = request.username
        
        print(f"User joined: {username} (ID: {user_id})")
        
        # Store the username
        with user_lock:
            self.usernames[user_id] = username
        
        return JoinResponse(success=True, message=f"Welcome to the chat, {username}!")
    
    def ChatStream(self, request_iterator, context):
        """Handle the bidirectional streaming RPC for chat messages"""
        user_id = None
        username = None
        
        # Extract user_id from metadata if available
        metadata = dict(context.invocation_metadata())
        if 'user_id' in metadata:
            user_id = metadata['user_id']
            username = self.usernames.get(user_id, "Unknown User")
            print(f"Starting chat stream for user {username} (ID: {user_id})")
        
        # Create a message queue for this client
        message_queue = []
        queue_condition = threading.Condition()
        
        try:
            # Register this client's stream
            if user_id:
                with user_lock:
                    connected_users[user_id] = (message_queue, queue_condition)
                    print(f"Registered client stream for {username} (ID: {user_id})")
            
            # Start a thread to process incoming messages from this client
            def process_client_messages():
                nonlocal user_id, username
                
                for chat_message in request_iterator:
                    # First message establishes connection if not already set
                    if not user_id:
                        user_id = chat_message.user_id
                        username = chat_message.username
                        with user_lock:
                            self.usernames[user_id] = username
                            # Register the client if not already registered
                            if user_id not in connected_users:
                                connected_users[user_id] = (message_queue, queue_condition)
                        print(f"Established stream for {username} (ID: {user_id})")
                    
                    # Skip empty messages (heartbeat messages)
                    if not chat_message.message:
                        continue
                    
                    # Add timestamp if not present
                    if not chat_message.timestamp:
                        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        chat_message = ChatMessage(
                            user_id=chat_message.user_id,
                            username=chat_message.username,
                            message=chat_message.message,
                            timestamp=now
                        )
                    
                    print(f"[{chat_message.timestamp}] {username}: {chat_message.message}")
                    
                    # Store message in history
                    with history_lock:
                        message_history.append(chat_message)
                        if len(message_history) > 1000:
                            message_history.pop(0)
                    
                    # Broadcast to all clients
                    with user_lock:
                        active_users = list(connected_users.keys())
                    
                    print(f"Broadcasting message: '{chat_message.message}' from {username} to {len(active_users)} clients")
                    
                    for client_id in active_users:
                        if client_id != user_id:  # Don't send to self
                            try:
                                with user_lock:
                                    if client_id in connected_users:
                                        client_queue, client_condition = connected_users[client_id]
                                        with client_condition:
                                            client_queue.append(chat_message)
                                            client_condition.notify()
                                            print(f"  - Queued message for user ID: {client_id}")
                            except Exception as e:
                                print(f"Error sending to {client_id}: {e}")
            
            # Start the client message processing thread
            client_thread = threading.Thread(target=process_client_messages)
            client_thread.daemon = True
            client_thread.start()
            
            # Process outgoing messages to this client
            while True:
                messages_to_send = []
                
                # Wait for messages to be queued for this client
                with queue_condition:
                    while not message_queue and user_id in connected_users:
                        queue_condition.wait(timeout=1.0)  # Wait with timeout
                    
                    # Check if client is still connected
                    if user_id not in connected_users:
                        print(f"Client disconnected: {username} (ID: {user_id})")
                        break
                    
                    # Get all queued messages
                    if message_queue:
                        messages_to_send = message_queue.copy()
                        message_queue.clear()
                
                # Send all queued messages
                for msg in messages_to_send:
                    print(f"  - Sending message to {username}: {msg.message}")
                    yield msg
        
        except Exception as e:
            print(f"Error in ChatStream for {username}: {e}")
        finally:
            # Clean up when a user disconnects
            if user_id:
                print(f"User left: {username} (ID: {user_id})")
                with user_lock:
                    if user_id in connected_users:
                        del connected_users[user_id]
                    if user_id in self.usernames:
                        self.usernames.pop(user_id, None)
                
                # Notify other users that this user has left
                system_msg = ChatMessage(
                    user_id="system",
                    username="System",
                    message=f"{username} has left the chat",
                    timestamp=datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                )
                with history_lock:
                    message_history.append(system_msg)
                
                # Broadcast the system message
                with user_lock:
                    active_users = list(connected_users.keys())
                
                for client_id in active_users:
                    try:
                        with user_lock:
                            if client_id in connected_users:
                                client_queue, client_condition = connected_users[client_id]
                                with client_condition:
                                    client_queue.append(system_msg)
                                    client_condition.notify()
                    except Exception as e:
                        print(f"Error sending departure message to {client_id}: {e}")
    
    def SendMessages(self, request_iterator, context):
        """Handle client streaming RPC for sending multiple messages at once"""
        user_id = None
        username = None
        message_count = 0
        
        # Extract user_id from metadata if available
        metadata = dict(context.invocation_metadata())
        if 'user_id' in metadata:
            user_id = metadata['user_id']
            username = self.usernames.get(user_id, "Unknown User")
            
        try:
            # Process all incoming messages from this client
            for chat_message in request_iterator:
                message_count += 1
                
                # Skip empty messages
                if not chat_message.message:
                    continue
                    
                if not user_id:
                    # If not set via metadata, get from first message
                    user_id = chat_message.user_id
                    username = chat_message.username
                    
                    with user_lock:
                        self.usernames[user_id] = username
                
                # Add timestamp if not present
                if not chat_message.timestamp:
                    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    chat_message = ChatMessage(
                        user_id=chat_message.user_id,
                        username=chat_message.username,
                        message=chat_message.message,
                        timestamp=now
                    )
                
                print(f"[{chat_message.timestamp}] Batch message from {username}: {chat_message.message}")
                
                # Store message in history
                with history_lock:
                    message_history.append(chat_message)
                
                # Broadcast to other clients
                self._broadcast_message(chat_message)
                
            return MessageResponse(
                success=True,
                message_count=message_count,
                summary=f"Successfully processed {message_count} messages from {username}"
            )
                
        except Exception as e:
            print(f"Error in SendMessages for {username}: {e}")
            return MessageResponse(
                success=False,
                message_count=message_count,
                summary=f"Error processing messages: {str(e)}"
            )
    
    def GetChatHistory(self, request, context):
        """Handle server streaming RPC for retrieving chat history"""
        user_id = request.user_id
        username = self.usernames.get(user_id, "Unknown User")
        
        limit = request.message_limit if request.message_limit > 0 else 50
        include_system = request.include_system_messages
        
        print(f"User {username} requested chat history (limit: {limit}, include system: {include_system})")
        
        # Copy history to avoid blocking other operations
        with history_lock:
            history_copy = message_history.copy()
        
        # Filter history based on request parameters
        count = 0
        for message in reversed(history_copy):
            # Skip if system message and not requested
            if not include_system and message.user_id == "system":
                continue
                
            yield message
            count += 1
            
            # Stop if we've reached the limit
            if count >= limit:
                break
    
    def _broadcast_message(self, message, exclude_user_id=None):
        """Broadcast a message to all connected clients except the sender"""
        # Store message in history
        with history_lock:
            message_history.append(message)
            
            # Keep history at a reasonable size (e.g., 1000 messages)
            if len(message_history) > 1000:
                message_history.pop(0)
        
        with user_lock:
            # Make a copy of the user IDs to avoid dictionary modification during iteration
            user_ids = list(connected_users.keys())
        
        print(f"Broadcasting message: '{message.message}' from {message.username} to {len(user_ids)} clients")
        
        for user_id in user_ids:
            # Don't exclude the sender to ensure bidirectional consistency
            # The client will handle ignoring duplicate messages
            try:
                with user_lock:
                    if user_id in connected_users:
                        queue, condition = connected_users[user_id]
                        with condition:
                            queue.append(message)
                            condition.notify_all()  # Use notify_all to wake up all waiting threads
                            print(f"  - Queued message for user ID: {user_id}")
            except Exception as e:
                print(f"Error queuing message for {user_id}: {e}")
    
    def _broadcast_user_joined(self, user_id, username):
        """Broadcast a system message that a new user has joined"""
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        system_message = ChatMessage(
            user_id="system",
            username="System",
            message=f"{username} has joined the chat",
            timestamp=now
        )
        self._broadcast_message(system_message, exclude_user_id=user_id)
    
    def _broadcast_user_left(self, user_id, username):
        """Broadcast a system message that a user has left"""
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        system_message = ChatMessage(
            user_id="system",
            username="System",
            message=f"{username} has left the chat",
            timestamp=now
        )
        self._broadcast_message(system_message)


def serve():
    """Start the gRPC server"""
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    add_ChatServiceServicer_to_server(ChatServicer(), server)
    server_address = '[::]:50051'  # Listen on port 50051
    server.add_insecure_port(server_address)
    server.start()
    print(f"Server started on {server_address}")
    
    try:
        while True:
            time.sleep(86400)  # Sleep for a day
    except KeyboardInterrupt:
        server.stop(0)
        print("Server stopped")


if __name__ == '__main__':
    serve()