import os
import subprocess
import math
import json
import asyncio
import telebot
from telebot.async_telebot import AsyncTeleBot
from telebot import apihelper
from flask import Flask, request, render_template, jsonify
from werkzeug.utils import secure_filename
import threading
import time
import uuid
import shutil

# Configuration
apihelper.TIMEOUT = 600
TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', "8396391757:AAFS0YHU0YniXvOxrocNab2uAeY56Cu4GKA")
CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', "898142325")
WEBHOOK_URL = os.environ.get('WEBHOOK_URL')

# App initialization
app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['CHUNKS_FOLDER'] = 'chunks'
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024  # 10MB chunks
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['CHUNKS_FOLDER'], exist_ok=True)

# Bot setup
bot = AsyncTeleBot(TOKEN)
bot_ready = False
bot_event_loop = None

# Check FFmpeg
def check_ffmpeg():
    try:
        subprocess.run(['ffmpeg', '-version'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        subprocess.run(['ffprobe', '-version'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False

if not check_ffmpeg():
    print("Error: FFmpeg not installed. Please install FFmpeg to continue.")
    exit(1)

# Video processing
async def process_video(video_path, message_id):
    try:
        output_folder = f"video_parts_{message_id}"
        os.makedirs(output_folder, exist_ok=True)
        
        # Get video info
        def get_video_info(video_path):
            cmd = [
                'ffprobe', '-v', 'error', '-select_streams', 'v:0',
                '-show_entries', 'stream=duration,r_frame_rate,width,height,bit_rate,pix_fmt',
                '-show_entries', 'format=duration,bit_rate', '-of', 'json', video_path
            ]
            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            info = json.loads(result.stdout)
            
            video_stream = info['streams'][0]
            return {
                'duration': float(info['format']['duration']),
                'fps': eval(video_stream['r_frame_rate']),
                'width': int(video_stream['width']),
                'height': int(video_stream['height']),
                'bit_rate': video_stream.get('bit_rate') or info['format'].get('bit_rate'),
                'pix_fmt': video_stream.get('pix_fmt', 'yuv420p')
            }
        
        video_info = get_video_info(video_path)
        duration = video_info['duration']
        num_parts = math.ceil(duration / 15)
        
        # Target dimensions
        target_width = 1080
        target_height = 1920
        top_bar_height = int(target_height * 0.2)
        bottom_bar_height = int(target_height * 0.2)
        middle_height = target_height - top_bar_height - bottom_bar_height
        
        # Font path
        font_path = next((path for path in [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf"
        ] if os.path.exists(path)), "arial")
        
        await bot.send_message(CHAT_ID, f"🎬 Processing video... Found {num_parts} parts. Preserving original quality.")
        
        # Process each part
        for i in range(num_parts):
            start_time = i * 15
            end_time = min((i + 1) * 15, duration)
            part_duration = end_time - start_time
            
            if part_duration < 0.1:
                continue
                
            output_path = os.path.join(output_folder, f"part_{i+1}.mp4")
            target_bitrate = int(video_info['bit_rate']) if video_info['bit_rate'] else 8000000
            
            # FFmpeg command
            cmd = [
                'ffmpeg', '-ss', str(start_time), '-i', video_path, '-t', str(part_duration),
                '-vf', f'scale={target_width}:{middle_height}:force_original_aspect_ratio=decrease,'
                       f'pad={target_width}:{middle_height}:(ow-iw)/2:(oh-ih)/2:color=black,'
                       f'pad={target_width}:{target_height}:0:{top_bar_height}:color=black,'
                       f'drawtext=text=\'Part {i+1}\':fontfile={font_path}:fontsize=80:'
                       f'x=(w-tw)/2:y=(h-th)/10:fontcolor=white:shadowcolor=white:shadowx=3:shadowy=3',
                '-c:v', 'libx264', '-preset', 'slow', '-crf', '18',
                '-pix_fmt', video_info['pix_fmt'], '-b:v', f'{target_bitrate}',
                '-maxrate', f'{int(target_bitrate * 1.5)}', '-bufsize', f'{int(target_bitrate * 2)}',
                '-c:a', 'aac', '-b:a', '320k', '-movflags', '+faststart',
                '-avoid_negative_ts', 'make_zero', '-fflags', '+genpts', '-y', output_path
            ]
            
            subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
            
            # Send part
            with open(output_path, 'rb') as video_file:
                await bot.send_video(CHAT_ID, video_file, caption=f"Part {i+1}/{num_parts}")
            
            os.remove(output_path)
            await asyncio.sleep(1)
        
        os.rmdir(output_folder)
        await bot.send_message(CHAT_ID, "✅ All parts processed successfully with high quality!")
        
    except Exception as e:
        await bot.send_message(CHAT_ID, f"❌ Error processing video: {str(e)}")
        raise e
    finally:
        if os.path.exists(video_path):
            os.remove(video_path)

# Bot runner
def run_bot_event_loop():
    global bot_event_loop, bot_ready
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    bot_event_loop = loop
    
    if WEBHOOK_URL:
        try:
            loop.run_until_complete(bot.set_webhook(url=WEBHOOK_URL))
            print(f"Webhook set to: {WEBHOOK_URL}")
        except Exception as e:
            print(f"Failed to set webhook: {e}")
    
    try:
        loop.run_until_complete(bot.send_message(CHAT_ID, "✅ Bot is online and ready to process videos!"))
        bot_ready = True
    except Exception as e:
        print(f"Bot connection test failed: {str(e)}")
        bot_ready = False
    
    loop.run_forever()

# Start bot thread
threading.Thread(target=run_bot_event_loop, daemon=True).start()
while not bot_ready:
    time.sleep(1)
print("Bot is ready!")

# Webhook handler
@app.route('/webhook', methods=['POST'])
def webhook():
    if request.headers.get('content-type') == 'application/json':
        update = telebot.types.Update.de_json(request.get_data(as_text=True))
        asyncio.run_coroutine_threadsafe(bot.process_new_updates([update]), bot_event_loop)
        return jsonify({"status": "ok"})
    return jsonify({"status": "error"})

# Web routes
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/init_upload', methods=['POST'])
def init_upload():
    if not bot_ready:
        return jsonify({"error": "Bot not ready"}), 503
    
    upload_id = str(uuid.uuid4())
    os.makedirs(os.path.join(app.config['CHUNKS_FOLDER'], upload_id), exist_ok=True)
    return jsonify({"upload_id": upload_id})

@app.route('/upload_chunk', methods=['POST'])
def upload_chunk():
    if not bot_ready:
        return jsonify({"error": "Bot not ready"}), 503
    
    upload_id = request.form.get('upload_id')
    chunk_index = request.form.get('chunk_index')
    chunk_file = os.path.join(app.config['CHUNKS_FOLDER'], upload_id, f"chunk_{chunk_index}")
    request.files.get('chunk').save(chunk_file)
    return jsonify({"success": True})

@app.route('/complete_upload', methods=['POST'])
def complete_upload():
    if not bot_ready:
        return jsonify({"error": "Bot not ready"}), 503
    
    upload_id = request.form.get('upload_id')
    file_name = request.form.get('file_name')
    upload_dir = os.path.join(app.config['CHUNKS_FOLDER'], upload_id)
    
    # Combine chunks
    video_path = os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(file_name))
    with open(video_path, 'wb') as outfile:
        for chunk_file in sorted(os.listdir(upload_dir), key=lambda x: int(x.split('_')[1])):
            with open(os.path.join(upload_dir, chunk_file), 'rb') as infile:
                outfile.write(infile.read())
    
    shutil.rmtree(upload_dir)
    asyncio.run_coroutine_threadsafe(process_video(video_path, int(time.time())), bot_event_loop)
    return jsonify({"success": True, "message": "Video uploaded and processing started."})

# Create template
os.makedirs('templates', exist_ok=True)
with open('templates/index.html', 'w') as f:
    f.write("""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Video Processor</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
        }
        body {
            background: linear-gradient(135deg, #1a2a6c, #b21f1f, #fdbb2d);
            min-height: 100vh;
            display: flex;
            justify-content: center;
            align-items: center;
            padding: 20px;
        }
        .container {
            background: rgba(255, 255, 255, 0.95);
            border-radius: 20px;
            box-shadow: 0 15px 35px rgba(0, 0, 0, 0.2);
            width: 100%;
            max-width: 600px;
            padding: 40px;
            text-align: center;
        }
        h1 {
            color: #333;
            margin-bottom: 10px;
            font-size: 2.5rem;
            background: linear-gradient(45deg, #1a2a6c, #b21f1f);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        .subtitle {
            color: #666;
            margin-bottom: 30px;
            font-size: 1.1rem;
        }
        .upload-area {
            border: 3px dashed #1a2a6c;
            border-radius: 15px;
            padding: 40px 20px;
            margin: 30px 0;
            background: rgba(26, 42, 108, 0.05);
            transition: all 0.3s ease;
            cursor: pointer;
        }
        .upload-area:hover {
            border-color: #b21f1f;
            background: rgba(178, 31, 31, 0.05);
        }
        .upload-area.dragover {
            background: rgba(253, 187, 45, 0.2);
            border-color: #fdbb2d;
        }
        .upload-icon {
            font-size: 3rem;
            color: #1a2a6c;
            margin-bottom: 15px;
        }
        .upload-text {
            color: #555;
            margin-bottom: 15px;
            font-size: 1.2rem;
        }
        .file-input {
            display: none;
        }
        .upload-btn {
            background: linear-gradient(45deg, #1a2a6c, #b21f1f);
            color: white;
            border: none;
            padding: 12px 30px;
            border-radius: 50px;
            font-size: 1rem;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.3s ease;
        }
        .upload-btn:hover {
            transform: translateY(-3px);
            box-shadow: 0 10px 20px rgba(0, 0, 0, 0.2);
        }
        .progress-container {
            margin: 30px 0;
            display: none;
        }
        .progress-bar {
            height: 10px;
            background: #e0e0e0;
            border-radius: 10px;
            overflow: hidden;
            margin-bottom: 10px;
        }
        .progress {
            height: 100%;
            background: linear-gradient(90deg, #1a2a6c, #b21f1f);
            width: 0%;
            transition: width 0.3s ease;
        }
        .status {
            padding: 15px;
            border-radius: 10px;
            margin-top: 20px;
            font-weight: 500;
            display: none;
        }
        .success {
            background: rgba(40, 167, 69, 0.1);
            color: #28a745;
            border: 1px solid rgba(40, 167, 69, 0.3);
        }
        .error {
            background: rgba(220, 53, 69, 0.1);
            color: #dc3545;
            border: 1px solid rgba(220, 53, 69, 0.3);
        }
        .features {
            display: flex;
            justify-content: space-around;
            margin: 30px 0;
            flex-wrap: wrap;
        }
        .feature {
            flex: 1;
            min-width: 150px;
            margin: 10px;
            padding: 15px;
            background: rgba(26, 42, 108, 0.05);
            border-radius: 10px;
        }
        .feature-icon {
            font-size: 2rem;
            margin-bottom: 10px;
        }
        .feature-title {
            font-weight: 600;
            margin-bottom: 5px;
            color: #333;
        }
        .feature-desc {
            color: #666;
            font-size: 0.9rem;
        }
        .footer {
            margin-top: 30px;
            color: #666;
            font-size: 0.9rem;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>Video Processor</h1>
        <p class="subtitle">Upload videos and get processed parts on Telegram</p>
        
        <div class="features">
            <div class="feature">
                <div class="feature-icon">🎬</div>
                <div class="feature-title">High Quality</div>
                <div class="feature-desc">Preserves original resolution and bitrate</div>
            </div>
            <div class="feature">
                <div class="feature-icon">✂️</div>
                <div class="feature-title">Smart Splitting</div>
                <div class="feature-desc">15-second parts with perfect aspect ratio</div>
            </div>
            <div class="feature">
                <div class="feature-icon">🚀</div>
                <div class="feature-title">Fast Processing</div>
                <div class="feature-desc">Efficient chunked upload system</div>
            </div>
        </div>
        
        <div class="upload-area" id="upload-area">
            <div class="upload-icon">📁</div>
            <p class="upload-text">Drag & drop your video here</p>
            <button class="upload-btn" id="upload-btn">Browse Files</button>
            <input type="file" id="file-input" class="file-input" accept="video/*">
        </div>
        
        <div class="progress-container" id="progress-container">
            <div class="progress-bar">
                <div class="progress" id="progress"></div>
            </div>
            <div id="progress-text">0%</div>
        </div>
        
        <div id="status" class="status"></div>
        
        <div class="footer">
            <p>Processed videos will be sent directly to your Telegram</p>
        </div>
    </div>

    <script>
        document.addEventListener('DOMContentLoaded', function() {
            const uploadArea = document.getElementById('upload-area');
            const fileInput = document.getElementById('file-input');
            const uploadBtn = document.getElementById('upload-btn');
            const progressContainer = document.getElementById('progress-container');
            const progressBar = document.getElementById('progress');
            const progressText = document.getElementById('progress-text');
            const statusDiv = document.getElementById('status');
            
            // Drag and drop events
            ['dragenter', 'dragover', 'dragleave', 'drop'].forEach(eventName => {
                uploadArea.addEventListener(eventName, preventDefaults, false);
            });
            
            function preventDefaults(e) {
                e.preventDefault();
                e.stopPropagation();
            }
            
            ['dragenter', 'dragover'].forEach(eventName => {
                uploadArea.addEventListener(eventName, highlight, false);
            });
            
            ['dragleave', 'drop'].forEach(eventName => {
                uploadArea.addEventListener(eventName, unhighlight, false);
            });
            
            function highlight() {
                uploadArea.classList.add('dragover');
            }
            
            function unhighlight() {
                uploadArea.classList.remove('dragover');
            }
            
            uploadArea.addEventListener('drop', handleDrop, false);
            
            function handleDrop(e) {
                const dt = e.dataTransfer;
                const files = dt.files;
                handleFiles(files);
            }
            
            uploadBtn.addEventListener('click', () => {
                fileInput.click();
            });
            
            fileInput.addEventListener('change', function() {
                handleFiles(this.files);
            });
            
            function handleFiles(files) {
                if (files.length > 0) {
                    uploadFile(files[0]);
                }
            }
            
            async function uploadFile(file) {
                const chunkSize = 5 * 1024 * 1024; // 5MB chunks
                const totalChunks = Math.ceil(file.size / chunkSize);
                
                uploadArea.style.display = 'none';
                progressContainer.style.display = 'block';
                statusDiv.style.display = 'none';
                
                try {
                    // Initialize upload
                    const initResponse = await fetch('/init_upload', { method: 'POST' });
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
                        progressText.textContent = progress + '%';
                        
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
                    resetUpload();
                    
                } catch (error) {
                    showStatus('Error: ' + error.message, false);
                    resetUpload();
                }
            }
            
            function showStatus(message, isSuccess) {
                statusDiv.textContent = message;
                statusDiv.className = 'status ' + (isSuccess ? 'success' : 'error');
                statusDiv.style.display = 'block';
            }
            
            function resetUpload() {
                setTimeout(() => {
                    uploadArea.style.display = 'block';
                    progressContainer.style.display = 'none';
                    progressBar.style.width = '0%';
                    progressText.textContent = '0%';
                    fileInput.value = '';
                }, 3000);
            }
        });
    </script>
</body>
</html>""")

if __name__ == '__main__':
    if WEBHOOK_URL:
        print(f"Using webhook URL: {WEBHOOK_URL}")
    else:
        print("Warning: WEBHOOK_URL environment variable not set.")
    
    app.run(debug=False, host='0.0.0.0', port=5000)
