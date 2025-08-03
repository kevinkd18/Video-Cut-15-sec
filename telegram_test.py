import os
import subprocess
import math
import json
import asyncio
import telebot
from telebot.async_telebot import AsyncTeleBot

# Bot initialization - REPLACE WITH YOUR ACTUAL TOKEN
TOKEN = "8396391757:AAFS0YHU0YniXvOxrocNab2uAeY56Cu4GKA"
bot = AsyncTeleBot(TOKEN)

# Video processing function
async def process_video(video_path, chat_id, message_id):
    try:
        # Create output folder
        output_folder = f"video_parts_{chat_id}_{message_id}"
        if not os.path.exists(output_folder):
            os.makedirs(output_folder)

        # Get video information using ffprobe
        def get_video_info(video_path):
            cmd = [
                'ffprobe',
                '-v', 'error',
                '-select_streams', 'v:0',
                '-show_entries', 'stream=duration,r_frame_rate,width,height',
                '-show_entries', 'format=duration',
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

            return {
                'duration': duration,
                'fps': fps,
                'width': width,
                'height': height
            }

        # Get video info
        video_info = get_video_info(video_path)
        duration = video_info['duration']
        fps = video_info['fps']
        width = video_info['width']
        height = video_info['height']

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

            # FFmpeg command with black background and white text
            cmd = [
                'ffmpeg',
                '-ss', str(start_time),
                '-i', video_path,
                '-t', str(part_duration_actual),
                '-vf',
                f'scale={target_width}:{middle_height}:force_original_aspect_ratio=decrease,pad={target_width}:{middle_height}:(ow-iw)/2:(oh-ih)/2:color=black,pad={target_width}:{target_height}:0:{top_bar_height}:color=black,drawtext=text=\'Part {i+1}\':fontfile={font_path}:fontsize=80:x=(w-tw)/2:y=(h-th)/10:fontcolor=white:shadowcolor=white:shadowx=3:shadowy=3',
                '-c:v', 'h264_nvenc',  # NVIDIA GPU encoding
                '-preset', 'fast',
                '-rc', 'vbr',  # Variable bitrate
                '-cq', '20',    # Quality level
                '-c:a', 'aac',
                '-b:a', '192k',
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

            # Send the processed part
            with open(output_path, 'rb') as video_file:
                await bot.send_video(chat_id, video_file, caption=f"Part {i+1}/{num_parts}")

            # Clean up the part file
            os.remove(output_path)

        # Clean up the output folder
        os.rmdir(output_folder)

        # Send completion message
        await bot.send_message(chat_id, "✅ All parts processed successfully!")

    except Exception as e:
        await bot.send_message(chat_id, f"❌ Error processing video: {str(e)}")
    finally:
        # Clean up original video
        if os.path.exists(video_path):
            os.remove(video_path)

# Handle video messages
@bot.message_handler(content_types=['video'])
async def handle_video(message):
    try:
        # Send processing message
        processing_msg = await bot.reply_to(message, "⏳ Processing your video...")

        # Download video
        file_info = await bot.get_file(message.video.file_id)
        downloaded_file = await bot.download_file(file_info.file_path)

        # Save video
        video_path = f"video_{message.chat.id}_{message.id}.mp4"
        with open(video_path, 'wb') as new_file:
            new_file.write(downloaded_file)

        # Process video
        await process_video(video_path, message.chat.id, message.id)

        # Delete processing message
        await bot.delete_message(message.chat.id, processing_msg.id)

    except Exception as e:
        await bot.reply_to(message, f"❌ Error: {str(e)}")

# Handle text messages
@bot.message_handler(func=lambda message: True)
async def handle_text(message):
    await bot.reply_to(message, "📹 Please send a video to process into parts")

# Start bot
async def main():
    print("Bot started...")
    await bot.delete_webhook()
    print("Webhook deleted successfully")
    await bot.polling(non_stop=True)

if __name__ == '__main__':
    asyncio.run(main())
