import eventlet
eventlet.monkey_patch()

import os
from flask import Flask, request, render_template, jsonify
from werkzeug.utils import secure_filename
import openai
import whisper
from pymongo import MongoClient
from datetime import datetime
from flask_socketio import SocketIO, emit
from config import Config, OPENAI_API_KEY

# Initialize Flask
app = Flask(__name__)
app.config.from_object(Config)

# Set OpenAI API key
openai.api_key = OPENAI_API_KEY

# Initialize Whisper model
model = whisper.load_model("base")

# MongoDB setup
mongo_client = MongoClient('mongodb://localhost:27017/')
db = mongo_client['meeting_db']
meetings_collection = db['meetings']

# Initialize Flask-SocketIO
socketio = SocketIO(app)

# Ensure upload directory exists
if not os.path.exists(app.config['UPLOAD_FOLDER']):
    os.makedirs(app.config['UPLOAD_FOLDER'])

def allowed_file(filename):
    """Check if the file has an allowed extension."""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in Config.ALLOWED_EXTENSIONS

def transcribe_audio(file_path):
    """Transcribe audio using Whisper model."""
    import shutil
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg not found. Please install ffmpeg and add it to your system's PATH.")
    result = model.transcribe(file_path)
    return result["text"]

def summarize_meeting(transcript):
    """Summarize meeting transcript using OpenAI GPT."""
    prompt = f"""Please summarize the following meeting transcript in a few sentences.\n\nTranscript:\n{transcript}\n"""
    try:
        response = openai.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are a professional meeting summarizer."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
            max_tokens=1000
        )
        return response.choices[0].message.content
    except Exception as e:
        print(f"OpenAI API error: {e}")
        raise

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({'error': 'No file part'}), 400
    file = request.files['file']
    if not file or not file.filename or file.filename == '':
        return jsonify({'error': 'No selected file'}), 400
    if not allowed_file(file.filename):
        return jsonify({'error': 'Invalid file type. Only MP3 and WAV are allowed.'}), 400
    
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
            'timestamp': datetime.utcnow()
        })
        
        os.remove(filepath)
        return jsonify({'summary': summary, 'transcript': transcript})
    
    except Exception as e:
        print(f"Upload error: {e}")
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

@app.route('/live')
def live_meeting():
    return render_template('live.html')

@socketio.on('audio_chunk')
def handle_audio_chunk(data):
    """Handle incoming audio chunk from client, transcribe, and emit transcript/notes."""
    import tempfile
    import base64

    audio_bytes = base64.b64decode(data['audio'])

    with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
        f.write(audio_bytes)
        temp_path = f.name

    try:
        transcript = transcribe_audio(temp_path)
        notes = summarize_meeting(transcript)
        emit('transcript', {'transcript': transcript, 'notes': notes})
    except Exception as e:
        emit('transcript', {'transcript': '', 'notes': '', 'error': str(e)})
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

@socketio.on('save_live_meeting')
def save_live_meeting(data):
    """Save live meeting transcript and notes to MongoDB."""
    try:
        meetings_collection.insert_one({
            'filename': 'Live Meeting',
            'summary': data.get('notes', ''),
            'transcript': data.get('transcript', ''),
            'timestamp': datetime.utcnow()
        })
        emit('save_status', {'success': True})
    except Exception as e:
        emit('save_status', {'success': False, 'error': str(e)})

@app.route('/update_notes', methods=['POST'])
def update_notes():
    if not request.is_json or not request.json:
        return jsonify({'error': 'Invalid or missing JSON.'}), 400
    data = request.json
    summary = data.get('summary')
    transcript = data.get('transcript')
    # Update the most recent meeting (for demo; in production, use an ID)
    meeting = meetings_collection.find_one(sort=[('timestamp', -1)])
    if not meeting:
        return jsonify({'error': 'No meeting found to update.'}), 404
    meetings_collection.update_one({'_id': meeting['_id']}, {'$set': {'summary': summary, 'transcript': transcript}})
    return jsonify({'success': True})

if __name__ == '__main__':
    socketio.run(app, debug=True)
print("OpenAI Key:", OPENAI_API_KEY)
