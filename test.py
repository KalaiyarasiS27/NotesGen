import eventlet
eventlet.monkey_patch()

import os
import shutil
import tempfile
import base64
from datetime import datetime

from flask import Flask, request, render_template, jsonify, Response
from flask_socketio import SocketIO, emit
from werkzeug.utils import secure_filename
from pymongo import MongoClient
import whisper

from jk import Config, GEMINI_API_KEY
from google.generativeai.client import configure
from google.generativeai.generative_models import GenerativeModel

# Flask App Setup
app = Flask(__name__)
app.config.from_object(Config)
socketio = SocketIO(app)
model = whisper.load_model("base")

# Gemini API Config
if GEMINI_API_KEY:
    configure(api_key=GEMINI_API_KEY)
else:
    raise ValueError("GEMINI_API_KEY is not set in environment variables")

# MongoDB Setup
mongo_client = MongoClient('mongodb://localhost:27017/')
db = mongo_client['meeting_db']
meetings_collection = db['meetings']

# Ensure upload directory exists
if not os.path.exists(app.config['UPLOAD_FOLDER']):
    os.makedirs(app.config['UPLOAD_FOLDER'])

# --- Utility Functions ---
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in Config.ALLOWED_EXTENSIONS

def transcribe_audio(file_path):
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg not found. Please install ffmpeg and add it to your system's PATH.")
    result = model.transcribe(file_path)
    return result["text"]

def split_into_chunks(text, max_tokens=3000):
    sentences = text.split('. ')
    chunks, chunk = [], ""
    for sentence in sentences:
        if len(chunk + sentence) < max_tokens:
            chunk += sentence + '. '
        else:
            chunks.append(chunk.strip())
            chunk = sentence + '. '
    if chunk:
        chunks.append(chunk.strip())
    return chunks

def summarize_meeting(transcript):
    try:
        model = GenerativeModel('gemini-1.5-flash')
        chunks = split_into_chunks(transcript)
        all_summaries = []
        for chunk in chunks:
            response = model.generate_content(f"Summarize this part of the meeting:\n\n{chunk}")
            all_summaries.append(response.text)
        return " ".join(all_summaries)
    except Exception as e:
        app.logger.error("Gemini API Error: %s", e)
        raise

def process_audio_file(audio_bytes, audio_format='audio/webm'):
    """Process audio file and return transcript and summary"""
    file_extension = '.webm' if 'webm' in audio_format else '.wav' if 'wav' in audio_format else '.mp4' if 'mp4' in audio_format else '.m4a' if 'm4a' in audio_format else '.webm'
    
    with tempfile.NamedTemporaryFile(suffix=file_extension, delete=False) as f:
        f.write(audio_bytes)
        temp_path = f.name

    try:
        if os.path.getsize(temp_path) < 500:
            return "", "Audio too short to process"
        
        transcript = transcribe_audio(temp_path)
        if transcript and isinstance(transcript, str) and transcript.strip():
            summary = summarize_meeting(transcript)
        else:
            summary = "No speech detected in this audio segment."
        
        return transcript, summary
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

# --- Routes ---
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({'error': 'No file part'}), 400
    file = request.files['file']
    if not file or not file.filename or not allowed_file(file.filename):
        return jsonify({'error': 'Invalid file'}), 400

    filename = secure_filename(file.filename)
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)

    try:
        file.save(filepath)
        if os.path.getsize(filepath) > app.config['MAX_CONTENT_LENGTH']:
            os.remove(filepath)
            return jsonify({'error': 'File is too large. Maximum size is 16MB.'}), 400

        transcript = transcribe_audio(filepath)
        summary = summarize_meeting(transcript)

        meetings_collection.insert_one({
            'filename': filename,
            'summary': summary,
            'transcript': transcript,
            'timestamp': datetime.utcnow(),
            'meeting_type': 'upload'
        })

        os.remove(filepath)
        return jsonify({'summary': summary, 'transcript': transcript})

    except Exception as e:
        app.logger.error("Upload error: %s", e)
        if os.path.exists(filepath):
            os.remove(filepath)
        return jsonify({'error': str(e)}), 500

@app.route('/meetings', methods=['GET'])
def get_meetings():
    meetings = list(meetings_collection.find({}, {'_id': 0}))
    meetings.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
    return jsonify({'meetings': meetings})

@app.route('/meetings/history')
def meetings_history():
    meetings = list(meetings_collection.find({}, {'_id': 0}))
    meetings.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
    return render_template('meetings.html', meetings=meetings)

@app.route('/download/<filename>')
def download_transcript(filename):
    meeting = meetings_collection.find_one({'filename': filename})
    if not meeting:
        return jsonify({'error': 'Meeting not found'}), 404
    
    transcript_content = f"""Meeting Transcript
Filename: {meeting['filename']}
Date: {meeting['timestamp'].strftime('%Y-%m-%d %H:%M:%S') if meeting['timestamp'] else 'N/A'}

SUMMARY:
{meeting['summary']}

TRANSCRIPT:
{meeting['transcript']}
"""
    
    response = Response(transcript_content, mimetype='text/plain')
    response.headers['Content-Disposition'] = f'attachment; filename="{filename}_transcript.txt"'
    return response

@app.route('/meeting/<filename>')
def get_meeting(filename):
    meeting = meetings_collection.find_one({'filename': filename})
    if not meeting:
        return jsonify({'error': 'Meeting not found'}), 404
    meeting['_id'] = str(meeting['_id'])
    return jsonify(meeting)

@app.route('/meeting/<filename>', methods=['PUT'])
def update_meeting(filename):
    try:
        data = request.get_json()
        if not data or 'summary' not in data or 'transcript' not in data:
            return jsonify({'error': 'Summary and transcript are required'}), 400
        
        result = meetings_collection.update_one(
            {'filename': filename},
            {
                '$set': {
                    'summary': data['summary'],
                    'transcript': data['transcript'],
                    'updated_at': datetime.utcnow()
                }
            }
        )
        
        if result.matched_count == 0:
            return jsonify({'error': 'Meeting not found'}), 404
        
        return jsonify({
            'success': True,
            'message': 'Meeting updated successfully',
            'filename': filename
        })
        
    except Exception as e:
        app.logger.error(f"Error updating meeting {filename}: {str(e)}")
        return jsonify({'error': f'Failed to update meeting: {str(e)}'}), 500

@app.route('/live')
def live_meeting():
    return render_template('live.html')

@app.route('/live-realtime')
def live_realtime_meeting():
    return render_template('live_realtime.html')

@app.route('/save_meeting', methods=['POST'])
def save_meeting():
    try:
        data = request.get_json()
        transcript = data.get('transcript', '')
        summary = data.get('summary', '')
        meeting_type = data.get('meeting_type', 'recorded')
        if not transcript or not summary:
            return jsonify({'error': 'Transcript and summary are required'}), 400
        timestamp = datetime.utcnow()
        filename = f"Recorded_Meeting_{timestamp.strftime('%Y%m%d_%H%M%S')}"
        meeting_data = {
            'filename': filename,
            'summary': summary,
            'transcript': transcript,
            'timestamp': timestamp,
            'meeting_type': meeting_type
        }
        result = meetings_collection.insert_one(meeting_data)
        return jsonify({'success': True, 'message': 'Meeting saved successfully!', 'meeting_id': str(result.inserted_id), 'filename': filename})
    except Exception as e:
        app.logger.error(f"Save meeting error: {str(e)}")
        return jsonify({'error': f'Failed to save meeting: {str(e)}'}), 500

# --- Socket Handlers ---
@socketio.on('audio_chunk')
def handle_audio_chunk(data):
    try:
        audio_bytes = base64.b64decode(data['audio'])
        transcript, notes = process_audio_file(audio_bytes, data.get('format', 'audio/webm'))
        
        emit('transcript', {
            'transcript': transcript, 
            'notes': notes,
            'success': True
        })
        
    except Exception as e:
        app.logger.error(f"Audio chunk error: {str(e)}")
        emit('transcript', {
            'transcript': '', 
            'notes': '', 
            'error': str(e),
            'success': False
        })

@socketio.on('save_live_meeting')
def save_live_meeting(data):
    try:
        transcript = data.get('transcript', '')
        notes = data.get('notes', '')
        
        if not transcript and not notes:
            emit('save_status', {'success': False, 'error': 'No transcript or notes to save'})
            return
        
        if transcript and len(transcript.strip()) < 10:
            emit('save_status', {'success': False, 'error': 'No meaningful speech detected'})
            return
        
        timestamp = datetime.utcnow()
        filename = f"Live_Meeting_{timestamp.strftime('%Y%m%d_%H%M%S')}"
        
        meeting_data = {
            'filename': filename,
            'summary': notes,
            'transcript': transcript,
            'timestamp': timestamp,
            'meeting_type': data.get('meeting_type', 'live')
        }
        
        result = meetings_collection.insert_one(meeting_data)
        
        emit('save_status', {
            'success': True, 
            'message': 'Live meeting saved successfully!',
            'meeting_id': str(result.inserted_id),
            'filename': filename
        })
        
    except Exception as e:
        app.logger.error(f"Save live meeting error: {str(e)}")
        emit('save_status', {
            'success': False, 
            'error': f'Failed to save live meeting: {str(e)}'
        })

@socketio.on('transcribe_complete_audio')
def handle_complete_audio_transcription(data):
    try:
        audio_bytes = base64.b64decode(data['audio'])
        transcript, summary = process_audio_file(audio_bytes, data.get('format', 'audio/webm'))
        
        emit('transcription_complete', {
            'transcript': transcript, 
            'summary': summary,
            'success': True
        })
        
    except Exception as e:
        app.logger.error(f"Complete audio transcription error: {str(e)}")
        emit('transcription_complete', {
            'transcript': '', 
            'summary': '', 
            'error': str(e),
            'success': False
        })

@socketio.on('update_live_meeting')
def update_live_meeting(data):
    try:
        filename = data.get('filename')
        transcript = data.get('transcript')
        summary = data.get('summary')
        
        # Validate input data
        if not filename:
            emit('update_status', {'success': False, 'error': 'Filename is required'})
            return
            
        if transcript is None or summary is None:
            emit('update_status', {'success': False, 'error': 'Transcript and summary are required'})
            return
        
        app.logger.info(f"Updating live meeting: {filename}")
        app.logger.info(f"Transcript length: {len(transcript) if transcript else 0}")
        app.logger.info(f"Summary length: {len(summary) if summary else 0}")
        
        result = meetings_collection.update_one(
            {'filename': filename},
            {
                '$set': {
                    'transcript': transcript, 
                    'summary': summary, 
                    'updated_at': datetime.utcnow()
                }
            }
        )
        
        if result.matched_count > 0:
            app.logger.info(f"Successfully updated meeting: {filename}")
            emit('update_status', {'success': True, 'message': 'Meeting updated successfully'})
        else:
            app.logger.error(f"Meeting not found for update: {filename}")
            emit('update_status', {'success': False, 'error': 'Meeting not found'})
            
    except Exception as e:
        app.logger.error(f"Error updating live meeting: {str(e)}")
        emit('update_status', {'success': False, 'error': f'Failed to update meeting: {str(e)}'})

if __name__ == '__main__':
    socketio.run(app, debug=True)
