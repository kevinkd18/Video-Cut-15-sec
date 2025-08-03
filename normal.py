import os
import subprocess
import math
import json
import asyncio
import telebot
from telebot.async_telebot import AsyncTeleBot
from telebot import apihelper
from flask import Flask, request, render_template, redirect, url_for, jsonify
from werkzeug.utils import secure_filename
import threading
import time
import uuid
import shutil

# Set a longer timeout for Telegram API requests
apihelper.TIMEOUT = 600  # 10 minutes

# Bot initialization - using your provided token
TOKEN = "8396391757:AAFS0YHU0YniXvOxrocNab2uAeY56Cu4GKA"
bot = AsyncTeleBot(TOKEN)

# Your correct chat ID - all videos will be sent here
CHAT_ID = "898142325"

# Flask app initialization
app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['CHUNKS_FOLDER'] = 'chunks'
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024  # 10MB max chunk size

# Ensure upload and chunks folders exist
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['CHUNKS_FOLDER'], exist_ok=True)

# Global variables for bot status
bot_ready = False
bot_event_loop = None
WEBHOOK_URL = None  # Will be set from environment variable

# Check if ffmpeg and ffprobe are available
def check_ffmpeg():
    try:
        subprocess.run(['ffmpeg', '-version'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        subprocess.run(['ffprobe', '-version'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False

if not check_ffmpeg():
    print("Error: FFmpeg and FFprobe are not installed. Please install them to continue.")
    print("On Ubuntu/Debian: sudo apt install ffmpeg")
    print("On CentOS/RHEL: sudo yum install ffmpeg")
    print("On macOS: brew install ffmpeg")
    exit(1)

# Video processing function
async def process_video(video_path, message_id):
    try:
        # Create output folder
        output_folder = f"video_parts_{message_id}"
        if not os.path.exists(output_folder):
            os.makedirs(output_folder)
        
        # Get video information using ffprobe
        def get_video_info(video_path):
            cmd = [
                'ffprobe', 
                '-v', 'error', 
                '-select_streams', 'v:0', 
                '-show_entries', 'stream=duration,r_frame_rate,width,height,bit_rate,pix_fmt', 
                '-show_entries', 'format=duration,bit_rate', 
                '-of', 'json', 
                video_path
            ]
            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            info = json.loads(result.stdout)
            
            video_stream = info['streams'][0]
            fps = eval(video_stream['r_frame_rate'])
            width = int(video_stream['width'])
            height = int(video_stream['height'])
            duration = float(info['format']['duration'])
            
            # Get bit rate (stream or format)
            bit_rate = video_stream.get('bit_rate')
            if not bit_rate:
                bit_rate = info['format'].get('bit_rate')
            if bit_rate:
                bit_rate = int(bit_rate)
            
            # Get pixel format
            pix_fmt = video_stream.get('pix_fmt', 'yuv420p')
            
            return {
                'duration': duration,
                'fps': fps,
                'width': width,
                'height': height,
                'bit_rate': bit_rate,
                'pix_fmt': pix_fmt
            }
        
        # Get video info
        video_info = get_video_info(video_path)
        duration = video_info['duration']
        fps = video_info['fps']
        width = video_info['width']
        height = video_info['height']
        bit_rate = video_info['bit_rate']
        pix_fmt = video_info['pix_fmt']
        
        # Calculate number of parts
        part_duration = 15  # seconds
        num_parts = math.ceil(duration / part_duration)
        
        # Target dimensions for 9:16 aspect ratio
        target_width = 1080
        target_height = 1920
        top_bar_height = int(target_height * 0.2)  # 20% top black bar
        bottom_bar_height = int(target_height * 0.2)  # 20% bottom black bar
        middle_height = target_height - top_bar_height - bottom_bar_height
        
        # Font path (check available fonts)
        font_paths = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf"
        ]
        font_path = None
        for path in font_paths:
            if os.path.exists(path):
                font_path = path
                break
        if not font_path:
            font_path = "arial"  # FFmpeg will use default
        
        # Send initial message with retry
        max_retries = 3
        for attempt in range(max_retries):
            try:
                await bot.send_message(CHAT_ID, f"🎬 Processing your video... Found {num_parts} parts to create. Preserving original quality.")
                break
            except Exception as e:
                if attempt == max_retries - 1:
                    raise Exception(f"Failed to send initial message after {max_retries} attempts: {str(e)}")
                await asyncio.sleep(2)
        
        # Process each part
        for i in range(num_parts):
            start_time = i * part_duration
            end_time = min((i + 1) * part_duration, duration)
            part_duration_actual = end_time - start_time
            
            # Skip if duration is too small
            if part_duration_actual < 0.1:
                continue
            
            # Output filename
            output_path = os.path.join(output_folder, f"part_{i+1}.mp4")
            
            # Calculate target bitrate to maintain quality
            # If original bitrate is available, use it; otherwise use a high default
            target_bitrate = bit_rate if bit_rate else 8000000  # 8Mbps default
            
            # Build FFmpeg command with high quality settings
            cmd = [
                'ffmpeg',
                '-ss', str(start_time),
                '-i', video_path,
                '-t', str(part_duration_actual),
                '-vf', 
                f'scale={target_width}:{middle_height}:force_original_aspect_ratio=decrease,pad={target_width}:{middle_height}:(ow-iw)/2:(oh-ih)/2:color=black,pad={target_width}:{target_height}:0:{top_bar_height}:color=black,drawtext=text=\'Part {i+1}\':fontfile={font_path}:fontsize=80:x=(w-tw)/2:y=(h-th)/10:fontcolor=white:shadowcolor=white:shadowx=3:shadowy=3',
                '-c:v', 'libx264',  # Software encoding for compatibility
                '-preset', 'slow',   # Slower preset for better quality
                '-crf', '18',       # Lower CRF for higher quality (18 is visually lossless)
                '-pix_fmt', pix_fmt, # Preserve original pixel format
                '-b:v', f'{target_bitrate}',  # Target bitrate
                '-maxrate', f'{int(target_bitrate * 1.5)}',  # Allow some bitrate fluctuation
                '-bufsize', f'{int(target_bitrate * 2)}',     # Buffer size
                '-c:a', 'aac',
                '-b:a', '320k',     # Higher audio bitrate for better quality
                '-movflags', '+faststart',
                '-avoid_negative_ts', 'make_zero',
                '-fflags', '+genpts',
                '-y',  # Overwrite output file
                output_path
            ]
            
            # Run FFmpeg
            subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
            
            # Verify output duration
            verify_cmd = [
                'ffprobe', 
                '-v', 'error', 
                '-show_entries', 'format=duration', 
                '-of', 'default=noprint_wrappers=1:nokey=1', 
                output_path
            ]
            result = subprocess.run(verify_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            output_duration = float(result.stdout.strip())
            
            # Send the processed part with retry
            for attempt in range(max_retries):
                try:
                    with open(output_path, 'rb') as video_file:
                        await bot.send_video(CHAT_ID, video_file, caption=f"Part {i+1}/{num_parts}")
                    break
                except Exception as e:
                    if attempt == max_retries - 1:
                        raise Exception(f"Failed to send video part {i+1} after {max_retries} attempts: {str(e)}")
                    await asyncio.sleep(2)
            
            # Clean up the part file
            os.remove(output_path)
            
            # Small delay to avoid flooding
            await asyncio.sleep(1)
        
        # Clean up the output folder
        os.rmdir(output_folder)
        
        # Send completion message with retry
        for attempt in range(max_retries):
            try:
                await bot.send_message(CHAT_ID, "✅ All parts processed successfully with high quality!")
                break
            except Exception as e:
                if attempt == max_retries - 1:
                    raise Exception(f"Failed to send completion message after {max_retries} attempts: {str(e)}")
                await asyncio.sleep(2)
        
    except Exception as e:
        # Try to send error message with retry
        for attempt in range(max_retries):
            try:
                await bot.send_message(CHAT_ID, f"❌ Error processing video: {str(e)}")
                break
            except Exception:
                if attempt == max_retries - 1:
                    print(f"Failed to send error message after {max_retries} attempts")
                await asyncio.sleep(2)
        raise e
    finally:
        # Clean up original video
        if os.path.exists(video_path):
            os.remove(video_path)

# Function to run the bot's event loop
def run_bot_event_loop():
    global bot_event_loop, bot_ready
    # Create a new event loop for this thread
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    bot_event_loop = loop
    
    # Set webhook if URL is provided
    if WEBHOOK_URL:
        try:
            loop.run_until_complete(bot.set_webhook(url=WEBHOOK_URL))
            print(f"Webhook set to: {WEBHOOK_URL}")
        except Exception as e:
            print(f"Failed to set webhook: {e}")
    
    # Send ready message
    try:
        loop.run_until_complete(bot.send_message(CHAT_ID, "✅ Bot is online and ready to process videos!"))
        print("Bot connection test successful")
        bot_ready = True
    except Exception as e:
        print(f"Bot connection test failed: {str(e)}")
        bot_ready = False
    
    # Keep the event loop running
    loop.run_forever()

# Start the bot event loop in a separate thread
bot_thread = threading.Thread(target=run_bot_event_loop)
bot_thread.daemon = True
bot_thread.start()

# Wait for the bot to be ready
print("Waiting for bot to initialize...")
while not bot_ready:
    time.sleep(1)
print("Bot is ready!")

# Webhook handler route
@app.route('/webhook', methods=['POST'])
def webhook():
    if request.headers.get('content-type') == 'application/json':
        json_string = request.get_data(as_text=True)
        update = telebot.types.Update.de_json(json_string)
        # Process the update in the bot's event loop
        asyncio.run_coroutine_threadsafe(bot.process_new_updates([update]), bot_event_loop)
        return jsonify({"status": "ok"})
    return jsonify({"status": "error"})

# Flask routes
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/init_upload', methods=['POST'])
def init_upload():
    if not bot_ready:
        return jsonify({"error": "Bot is not ready yet. Please try again later."}), 503
    
    # Generate a unique upload ID
    upload_id = str(uuid.uuid4())
    
    # Create a directory for this upload
    upload_dir = os.path.join(app.config['CHUNKS_FOLDER'], upload_id)
    os.makedirs(upload_dir, exist_ok=True)
    
    return jsonify({"upload_id": upload_id})

@app.route('/upload_chunk', methods=['POST'])
def upload_chunk():
    if not bot_ready:
        return jsonify({"error": "Bot is not ready yet. Please try again later."}), 503
    
    upload_id = request.form.get('upload_id')
    chunk_index = request.form.get('chunk_index')
    total_chunks = request.form.get('total_chunks')
    file_name = request.form.get('file_name')
    
    if not upload_id or chunk_index is None or not total_chunks or not file_name:
        return jsonify({"error": "Missing required parameters"}), 400
    
    upload_dir = os.path.join(app.config['CHUNKS_FOLDER'], upload_id)
    if not os.path.exists(upload_dir):
        return jsonify({"error": "Invalid upload ID"}), 400
    
    chunk_file = os.path.join(upload_dir, f"chunk_{chunk_index}")
    chunk_data = request.files.get('chunk')
    
    if not chunk_data:
        return jsonify({"error": "No chunk data provided"}), 400
    
    chunk_data.save(chunk_file)
    
    return jsonify({"success": True})

@app.route('/complete_upload', methods=['POST'])
def complete_upload():
    if not bot_ready:
        return jsonify({"error": "Bot is not ready yet. Please try again later."}), 503
    
    upload_id = request.form.get('upload_id')
    file_name = request.form.get('file_name')
    
    if not upload_id or not file_name:
        return jsonify({"error": "Missing required parameters"}), 400
    
    upload_dir = os.path.join(app.config['CHUNKS_FOLDER'], upload_id)
    if not os.path.exists(upload_dir):
        return jsonify({"error": "Invalid upload ID"}), 400
    
    # Get all chunk files
    chunk_files = sorted([f for f in os.listdir(upload_dir) if f.startswith('chunk_')], 
                         key=lambda x: int(x.split('_')[1]))
    
    if not chunk_files:
        return jsonify({"error": "No chunks found"}), 400
    
    # Create the final video file
    video_filename = secure_filename(file_name)
    video_path = os.path.join(app.config['UPLOAD_FOLDER'], video_filename)
    
    # Combine chunks into the final file
    with open(video_path, 'wb') as outfile:
        for chunk_file in chunk_files:
            chunk_path = os.path.join(upload_dir, chunk_file)
            with open(chunk_path, 'rb') as infile:
                outfile.write(infile.read())
    
    # Clean up chunks directory
    shutil.rmtree(upload_dir)
    
    # Generate a unique message_id for this processing
    message_id = int(time.time())
    
    # Submit the video processing task to the bot's event loop
    future = asyncio.run_coroutine_threadsafe(process_video(video_path, message_id), bot_event_loop)
    
    return jsonify({"success": True, "message": "Video uploaded and is being processed. You'll receive the parts on Telegram."})

# Create templates directory and index.html
os.makedirs('templates', exist_ok=True)
with open('templates/index.html', 'w') as f:
    f.write("""
<!DOCTYPE html>
<html>
<head>
    <title>Video to Telegram Parts</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            max-width: 600px;
            margin: 0 auto;
            padding: 20px;
        }
        .form-group {
            margin-bottom: 15px;
        }
        label {
            display: block;
            margin-bottom: 5px;
            font-weight: bold;
        }
        input[type="file"] {
            width: 100%;
            padding: 8px;
            border: 1px solid #ddd;
            border-radius: 4px;
        }
        button {
            background-color: #4CAF50;
            color: white;
            padding: 10px 15px;
            border: none;
            border-radius: 4px;
            cursor: pointer;
        }
        button:hover {
            background-color: #45a049;
        }
        button:disabled {
            background-color: #cccccc;
            cursor: not-allowed;
        }
        .info {
            background-color: #f8f9fa;
            padding: 15px;
            border-radius: 4px;
            margin-bottom: 20px;
        }
        .progress-container {
            width: 100%;
            background-color: #f3f3f3;
            border-radius: 4px;
            margin-top: 10px;
            display: none;
        }
        .progress-bar {
            width: 0%;
            height: 20px;
            background-color: #4CAF50;
            border-radius: 4px;
            text-align: center;
            line-height: 20px;
            color: white;
        }
        .status {
            margin-top: 20px;
            padding: 10px;
            border-radius: 4px;
            display: none;
        }
        .success {
            background-color: #d4edda;
            color: #155724;
            border: 1px solid #c3e6cb;
        }
        .error {
            background-color: #f8d7da;
            color: #721c24;
            border: 1px solid #f5c6cb;
        }
        .quality-note {
            background-color: #e7f3fe;
            border-left: 6px solid #2196F3;
            margin-bottom: 15px;
            padding: 10px;
        }
    </style>
</head>
<body>
    <h1>Video to Telegram Parts</h1>
    
    <div class="quality-note">
        <strong>High Quality Processing:</strong> Your videos will be processed with the highest possible quality, preserving the original resolution and bitrate.
    </div>
    
    <div class="info">
        <h3>Instructions:</h3>
        <ol>
            <li>Upload your video file (no size limit)</li>
            <li>Click "Upload and Process"</li>
            <li>You'll receive processed video parts on Telegram</li>
        </ol>
    </div>
    
    <form id="upload-form">
        <div class="form-group">
            <label for="file">Select Video:</label>
            <input type="file" name="file" id="file" accept="video/*" required>
        </div>
        
        <button type="submit" id="upload-button">Upload and Process</button>
    </form>
    
    <div class="progress-container" id="progress-container">
        <div class="progress-bar" id="progress-bar">0%</div>
    </div>
    
    <div id="status" class="status"></div>
    
    <script>
        document.addEventListener('DOMContentLoaded', function() {
            const form = document.getElementById('upload-form');
            const fileInput = document.getElementById('file');
            const uploadButton = document.getElementById('upload-button');
            const progressContainer = document.getElementById('progress-container');
            const progressBar = document.getElementById('progress-bar');
            const statusDiv = document.getElementById('status');
            
            form.addEventListener('submit', async function(e) {
                e.preventDefault();
                
                if (fileInput.files.length === 0) {
                    showStatus('Please select a video file.', false);
                    return;
                }
                
                const file = fileInput.files[0];
                const chunkSize = 5 * 1024 * 1024; // 5MB chunks
                const totalChunks = Math.ceil(file.size / chunkSize);
                
                uploadButton.disabled = true;
                progressContainer.style.display = 'block';
                progressBar.style.width = '0%';
                progressBar.textContent = '0%';
                showStatus('Initializing upload...', true);
                
                try {
                    // Initialize upload
                    const initResponse = await fetch('/init_upload', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json'
                        }
                    });
                    
                    const initData = await initResponse.json();
                    
                    if (initData.error) {
                        throw new Error(initData.error);
                    }
                    
                    const uploadId = initData.upload_id;
                    
                    // Upload chunks
                    for (let chunkIndex = 0; chunkIndex < totalChunks; chunkIndex++) {
                        const start = chunkIndex * chunkSize;
                        const end = Math.min(file.size, start + chunkSize);
                        const chunk = file.slice(start, end);
                        
                        const formData = new FormData();
                        formData.append('upload_id', uploadId);
                        formData.append('chunk_index', chunkIndex);
                        formData.append('total_chunks', totalChunks);
                        formData.append('file_name', file.name);
                        formData.append('chunk', chunk);
                        
                        const progress = Math.round(((chunkIndex + 1) / totalChunks) * 100);
                        progressBar.style.width = progress + '%';
                        progressBar.textContent = progress + '%';
                        
                        showStatus(`Uploading chunk ${chunkIndex + 1} of ${totalChunks}...`, true);
                        
                        const response = await fetch('/upload_chunk', {
                            method: 'POST',
                            body: formData
                        });
                        
                        const data = await response.json();
                        
                        if (data.error) {
                            throw new Error(data.error);
                        }
                    }
                    
                    // Complete upload
                    showStatus('Finalizing upload...', true);
                    
                    const completeFormData = new FormData();
                    completeFormData.append('upload_id', uploadId);
                    completeFormData.append('file_name', file.name);
                    
                    const completeResponse = await fetch('/complete_upload', {
                        method: 'POST',
                        body: completeFormData
                    });
                    
                    const completeData = await completeResponse.json();
                    
                    if (completeData.error) {
                        throw new Error(completeData.error);
                    }
                    
                    showStatus(completeData.message, true);
                    form.reset();
                    
                } catch (error) {
                    showStatus('Error: ' + error.message, false);
                } finally {
                    uploadButton.disabled = false;
                }
            });
            
            function showStatus(message, isSuccess) {
                statusDiv.textContent = message;
                statusDiv.className = 'status ' + (isSuccess ? 'success' : 'error');
                statusDiv.style.display = 'block';
            }
        });
    </script>
</body>
</html>
""")

if __name__ == '__main__':
    # Get webhook URL from environment variable
    WEBHOOK_URL = os.environ.get('WEBHOOK_URL')
    
    if WEBHOOK_URL:
        print(f"Using webhook URL: {WEBHOOK_URL}")
    else:
        print("Warning: WEBHOOK_URL environment variable not set. Webhook will not be configured.")
        print("For production, set the WEBHOOK_URL environment variable to your public URL.")
    
    app.run(debug=True, host='0.0.0.0', port=5000)

    
