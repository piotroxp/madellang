import uuid
import asyncio
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from typing import Dict, List, Optional

from room_manager import RoomManager
from audio_processor import AudioProcessor
from model_manager import ModelManager

import sys
sys.path.append("./deps/whisper")
sys.path.append("./deps/audio")
import whisper
import torchaudio
print(whisper.__version__)  # Should print the version or commit hash
print(torchaudio.__version__)
# Now use whisper as usual
model = whisper.load_model("turbo")
result = model.transcribe("./data/test/test.m4a")
print(result["text"])

app = FastAPI()

# Add CORS to allow connections from your frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify your frontend URL
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize components
model_manager = ModelManager()
audio_processor = AudioProcessor(model_manager)
room_manager = RoomManager(audio_processor)

@app.get("/")
async def root():
    return {"message": "Voice Translation API is running"}

@app.websocket("/ws/{room_id}")
async def websocket_endpoint(websocket: WebSocket, room_id: str, target_lang: str = "es"):
    """
    Handle WebSocket connections for a specific room
    """
    # Accept the connection
    await websocket.accept()
    
    # Generate a unique participant ID
    participant_id = str(uuid.uuid4())
    
    try:
        # Add the participant to the room
        await room_manager.add_participant(room_id, participant_id, websocket, target_lang)
        
        # Event loop to handle incoming messages
        while True:
            # Receive binary audio data
            audio_data = await websocket.receive_bytes()
            
            # Process the audio in the room
            await room_manager.process_audio(room_id, participant_id, audio_data)
            
    except WebSocketDisconnect:
        # Remove participant when disconnected
        await room_manager.remove_participant(room_id, participant_id)
    except Exception as e:
        print(f"Error: {e}")
        await room_manager.remove_participant(room_id, participant_id)

@app.get("/create-room")
async def create_room():
    """
    Create a new room and return its ID
    """
    room_id = await room_manager.create_room()
    return {"room_id": room_id}

@app.get("/rooms/{room_id}/participants")
async def get_room_participants(room_id: str):
    """
    Get the number of participants in a room
    """
    participants = await room_manager.get_participants(room_id)
    return {"participants": len(participants)}

# Run with: uvicorn main:app --host 0.0.0.0 --port 8000