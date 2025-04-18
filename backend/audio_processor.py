import asyncio
import io
import numpy as np
from typing import Optional, Dict, Any, Tuple
import time
import logging

logger = logging.getLogger(__name__)

class AudioProcessor:
    def __init__(self, model_manager, translation_service=None):
        self.model_manager = model_manager
        self.translation_service = translation_service
        self.mirror_mode = True  # Initialize mirror mode to True by default
        # Audio settings
        self.sample_rate = 16000
        self.chunk_size = 4096
        # Add buffer management
        self.buffer = {}  # room_id -> user_id -> buffer
        self.last_processing = {}  # room_id -> user_id -> timestamp
        self.min_process_interval = 0.5  # Minimum seconds between processing
        self.audio_buffers = {}  # Store audio chunks by user/room
        self.processing_lock = {}  # Prevent concurrent processing for same user
        logger.info("Audio processor initialized")
    
    async def process_audio(self, audio_data: bytes, target_lang: str, source_lang: Optional[str] = None) -> bytes:
        """
        Process audio through the Translation → TTS pipeline
        """
        # Process in a separate thread to avoid blocking
        return await asyncio.to_thread(self._process_audio_sync, audio_data, target_lang, source_lang)
    
    def _process_audio_sync(self, audio_data: bytes, target_lang: str, source_lang: Optional[str] = None) -> bytes:
        """
        Synchronous version of the audio processing pipeline using Whisper for translation
        """
        try:
            # Convert bytes to numpy array for processing
            audio_np = np.frombuffer(audio_data, dtype=np.int16)
            
            if self.translation_service:
                # Use Whisper for both STT and translation in one step
                result = self.translation_service.transcribe_and_translate(
                    audio_np, 
                    source_lang=source_lang, 
                    target_lang=target_lang
                )
                
                translated_text = result["translated_text"]
                detected_lang = result["detected_language"]
                
                # If we got no translation, skip further processing
                if not translated_text or translated_text.isspace():
                    return b""
                    
                # Log the translation process
                print(f"Translated: [{detected_lang}] {result['original_text']} -> [{target_lang}] {translated_text}")
            else:
                # Fallback to previous pipeline if no translation service
                transcript = self.model_manager.speech_to_text(audio_np)
                
                # Skip empty transcripts
                if not transcript or transcript.isspace():
                    return b""
                
                # Detect language if not provided
                if source_lang is None:
                    source_lang = self.model_manager.detect_language(audio_np)
                
                # Skip translation if source and target languages are the same
                if source_lang == target_lang:
                    translated_text = transcript
                else:
                    # Translation
                    translated_text = self.model_manager.translate_text(
                        transcript, source_lang, target_lang
                    )
            
            # Text-to-Speech 
            translated_audio = self.model_manager.text_to_speech(translated_text, target_lang)
            
            # Return the processed audio as bytes
            return translated_audio
            
        except Exception as e:
            print(f"Error in audio processing: {e}")
            return b""

    async def process_audio_chunk(self, room_id: str, user_id: str, 
                                 audio_chunk: bytes, target_lang: str, websocket) -> Optional[Dict]:
        """Process incoming audio chunk and return translation result"""
        try:
            # If mirror mode is enabled, simply echo the audio back
            if self.mirror_mode:
                logger.info(f"Mirroring audio back to sender: {len(audio_chunk)} bytes")
                # Send the audio directly back to the same websocket that sent it
                try:
                    await websocket.send_bytes(audio_chunk)
                    return True
                except Exception as e:
                    logger.error(f"Error sending mirrored audio: {str(e)}")
                    return False
                
            # Regular processing for translation mode
            # Add to buffer and get complete buffer
            complete_buffer = self._add_to_buffer(room_id, user_id, audio_chunk)
            
            # Convert audio bytes to numpy array
            audio_np = np.frombuffer(complete_buffer, dtype=np.float32)
            
            # Process only if we have enough audio data (at least 0.5 seconds)
            if len(audio_np) < 8000:  # Assuming 16kHz sample rate
                return None
                
            # Perform speech recognition and translation
            logger.debug(f"Processing {len(audio_np)} samples for user {user_id}")
            
            # Transcribe and translate
            result = self.translation_service.transcribe_and_translate(
                audio_np, target_lang=target_lang
            )
            
            # Only return results if we have text
            if result and result.get("translated_text") and len(result["translated_text"]) > 0:
                logger.info(f"Translation result: {result['translated_text'][:50]}...")
                
                # Clear buffer after successful processing
                self._clear_buffer(room_id, user_id)
                
                # Return the result for WebSocket transmission
                return {
                    "type": "translation_result",
                    "original_text": result.get("original_text", ""),
                    "translated_text": result.get("translated_text", ""),
                    "language": result.get("detected_language", "unknown"),
                    "user_id": user_id
                }
                
            return None
            
        except Exception as e:
            logger.error(f"Error processing audio chunk: {e}")
            return None

    def _add_to_buffer(self, room_id: str, user_id: str, audio_chunk: bytes) -> bytes:
        """Add audio chunk to user's buffer and return the complete buffer"""
        buffer_key = f"{room_id}_{user_id}"
        
        if buffer_key not in self.audio_buffers:
            self.audio_buffers[buffer_key] = bytearray()
            
        # Add new chunk to buffer
        self.audio_buffers[buffer_key].extend(audio_chunk)
        
        # Limit buffer size (keep last 5 seconds)
        max_buffer_size = 16000 * 4 * 5  # 5 seconds at 16kHz, 4 bytes per float32
        if len(self.audio_buffers[buffer_key]) > max_buffer_size:
            self.audio_buffers[buffer_key] = self.audio_buffers[buffer_key][-max_buffer_size:]
            
        return bytes(self.audio_buffers[buffer_key])
        
    def _clear_buffer(self, room_id: str, user_id: str):
        """Clear audio buffer for a user"""
        buffer_key = f"{room_id}_{user_id}"
        if buffer_key in self.audio_buffers:
            self.audio_buffers[buffer_key] = bytearray()

    def toggle_mirror_mode(self, enabled=None):
        """Toggle or set mirror mode"""
        if enabled is not None:
            self.mirror_mode = enabled
        else:
            self.mirror_mode = not self.mirror_mode
        
        logger.info(f"Audio mirror mode {'enabled' if self.mirror_mode else 'disabled'}")
        return self.mirror_mode