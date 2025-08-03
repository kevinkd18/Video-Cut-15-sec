import os
import subprocess
import time
import math
import json

# Start total timer
total_start_time = time.time()

# Input video path
input_video = "/content/input.mp4"

# Output folder
output_folder = "input_cut"

# Create output folder if not exists
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

    # Extract video stream info
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
video_info = get_video_info(input_video)
duration = video_info['duration']
fps = video_info['fps']
width = video_info['width']
height = video_info['height']

print(f"Video info: {width}x{height}, {fps:.2f} fps, {duration:.2f} seconds")

# Calculate number of parts
part_duration = 15  # seconds
num_parts = math.ceil(duration / part_duration)
print(f"Will create {num_parts} parts")

# Target dimensions for 9:16 aspect ratio
target_width = 1080
target_height = 1920
top_bar_height = int(target_height * 0.2)  # 20% top white bar
bottom_bar_height = int(target_height * 0.2)  # 20% bottom white bar
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
    print("Warning: No suitable font found. Using default font.")
    font_path = "arial"  # FFmpeg will use default

# Process each part using FFmpeg with frame-accurate cutting
for i in range(num_parts):
    part_start_time = time.time()

    start_time = i * part_duration
    end_time = min((i + 1) * part_duration, duration)
    part_duration_actual = end_time - start_time

    # Skip if duration is too small
    if part_duration_actual < 0.1:
        print(f"Skipping part {i+1} because duration is too small: {part_duration_actual:.2f}s")
        continue

    # Output filename
    output_path = os.path.join(output_folder, f"part_{i+1}.mp4")

    # FFmpeg command with frame-accurate cutting using keyframes
    cmd = [
        'ffmpeg',
        '-ss', str(start_time),
        '-i', input_video,
        '-t', str(part_duration_actual),
        '-vf',
        f'scale={target_width}:{middle_height}:force_original_aspect_ratio=decrease,pad={target_width}:{middle_height}:(ow-iw)/2:(oh-ih)/2:color=white,pad={target_width}:{target_height}:0:{top_bar_height}:color=white,drawtext=text=\'Part {i+1}\':fontfile={font_path}:fontsize=80:x=(w-tw)/2:y=(h-th)/10:fontcolor=black:shadowcolor=gray:shadowx=3:shadowy=3',
        '-c:v', 'h264_nvenc',  # NVIDIA GPU encoding
        '-preset', 'fast',
        '-rc', 'vbr',  # Variable bitrate
        '-cq', '20',    # Quality level (lower = better quality)
        '-c:a', 'aac',
        '-b:a', '192k',
        '-movflags', '+faststart',
        '-avoid_negative_ts', 'make_zero',  # Fix timestamp issues
        '-fflags', '+genpts',  # Generate presentation timestamps
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

    # Calculate and print part execution time
    part_end_time = time.time()
    part_time = part_end_time - part_start_time

    # Check if output duration matches expected
    duration_diff = abs(output_duration - part_duration_actual)
    if duration_diff > 0.1:  # Allow 0.1s difference
        print(f"WARNING: Duration mismatch for {output_path}")
        print(f"Expected: {part_duration_actual:.2f}s, Actual: {output_duration:.2f}s")

        # If duration is significantly off, recreate the part with more accurate method
        if duration_diff > 1.0:
            print(f"Recreating {output_path} with more accurate method...")

            # Use keyframe alignment for more accurate cutting
            cmd_accurate = [
                'ffmpeg',
                '-ss', str(start_time),
                '-i', input_video,
                '-to', str(end_time),
                '-vf',
                f'scale={target_width}:{middle_height}:force_original_aspect_ratio=decrease,pad={target_width}:{middle_height}:(ow-iw)/2:(oh-ih)/2:color=white,pad={target_width}:{target_height}:0:{top_bar_height}:color=white,drawtext=text=\'Part {i+1}\':fontfile={font_path}:fontsize=80:x=(w-tw)/2:y=(h-th)/10:fontcolor=black:shadowcolor=gray:shadowx=3:shadowy=3',
                '-c:v', 'h264_nvenc',  # NVIDIA GPU encoding
                '-preset', 'fast',
                '-rc', 'vbr',  # Variable bitrate
                '-cq', '20',    # Quality level (lower = better quality)
                '-c:a', 'aac',
                '-b:a', '192k',
                '-movflags', '+faststart',
                '-avoid_negative_ts', 'make_zero',  # Fix timestamp issues
                '-fflags', '+genpts',  # Generate presentation timestamps
                '-y',  # Overwrite output file
                output_path
            ]

            # Run accurate FFmpeg command
            subprocess.run(cmd_accurate, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)

            # Verify again
            result = subprocess.run(verify_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            output_duration = float(result.stdout.strip())
            print(f"Recreated: {output_path} | New duration: {output_duration:.2f}s")

    print(f"Created: {output_path} | Time taken: {part_time:.2f}s | Duration: {output_duration:.2f}s")

# Calculate and print total execution time
total_end_time = time.time()
total_time = total_end_time - total_start_time
print(f"\nAll parts created successfully!")
print(f"Total execution time: {total_time:.2f} seconds")
print(f"Average time per part: {total_time/num_parts:.2f} seconds")

# Create a verification script to check all parts
with open(os.path.join(output_folder, 'verify.py'), 'w') as f:
    f.write(f"""
import subprocess
import os

# Check all parts
parts = {num_parts}
total_duration = 0
for i in range(1, parts+1):
    output_path = os.path.join("{output_folder}", f"part_{{i}}.mp4")
    if not os.path.exists(output_path):
        print(f"ERROR: Part {{i}} does not exist!")
        continue

    cmd = [
        'ffprobe',
        '-v', 'error',
        '-show_entries', 'format=duration',
        '-of', 'default=noprint_wrappers=1:nokey=1',
        output_path
    ]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    duration = float(result.stdout.strip())
    total_duration += duration
    print(f"Part {{i}}: {{duration:.2f}} seconds")

print(f"\\nTotal duration of all parts: {{total_duration:.2f}} seconds")
print(f"Original video duration: {duration:.2f} seconds")
print(f"Difference: {{abs(total_duration - duration):.2f}} seconds")

# Check if any part is missing or corrupted
if abs(total_duration - duration) > 0.5:
    print("\\nWARNING: There might be missing or corrupted parts!")
else:
    print("\\nAll parts created successfully!")
""")

print(f"\nVerification script created: {os.path.join(output_folder, 'verify.py')}")
print("Run this script to check if all parts were created correctly.")
