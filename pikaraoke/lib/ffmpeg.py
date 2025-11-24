import logging
import subprocess

import ffmpeg


def get_media_duration(file_path):
    try:
        duration = ffmpeg.probe(file_path)["format"]["duration"]
        return round(float(duration))
    except:
        return None


def build_ffmpeg_cmd(
    fr,
    semitones=0,
    normalize_audio=True,
    buffer_fully_before_playback=False,
    avsync=0,
    cdg_pixel_scaling=False,
    original_sound=True,
    original_sound_volume=40,
):
    avsync = float(avsync)
    # use h/w acceleration on pi
    default_vcodec = "h264_v4l2m2m" if supports_hardware_h264_encoding() else "libx264"
    # just copy the video stream if it's an mp4 or webm file, since they are supported natively in html5
    # otherwise use the default h264 codec
    vcodec = (
        "copy" if fr.file_extension == ".mp4" or fr.file_extension == ".webm" else default_vcodec
    )
    vbitrate = "15M"  # seems to yield best results w/ h264_v4l2m2m on pi, recommended for 720p.

    # copy the audio stream if no transposition/normalization/mixing, otherwise re-encode with aac
    is_transposed = semitones != 0
    acodec = "aac" if is_transposed or normalize_audio or avsync != 0 or original_sound else "copy"

    input = ffmpeg.input(fr.file_path)

    # Prefer explicit stream selection: use audio stream 0 as the "original" (contains vocals)
    # and audio stream 1 as the "processed/instrumental" (no vocals). If a:1 isn't present,
    # fall back to the single-audio-stream behavior.
    try:
        orig_audio = input["a:0"]
        proc_audio = input["a:1"]
    except Exception:
        orig_audio = input.audio
        proc_audio = input.audio

    # Decide whether we actually need to process the proc audio stream.
    is_transposed = semitones != 0
    needs_processing = is_transposed or normalize_audio or avsync != 0

    # If no processing is required and the user does NOT want the original mixed back in,
    # just use the processed track (a:1 when present) as-is -- avoids creating filter_graphs.
    if not needs_processing and not original_sound:
        audio = proc_audio
    else:
        # If avsync is set, delay or trim both audio streams equally
        if avsync > 0:
            delay_ms = int(avsync * 1000)
            proc_audio = proc_audio.filter("adelay", f"{delay_ms}|{delay_ms}")
            orig_audio = orig_audio.filter("adelay", f"{delay_ms}|{delay_ms}")
        elif avsync < 0:
            proc_audio = proc_audio.filter("atrim", start=-avsync)
            orig_audio = orig_audio.filter("atrim", start=-avsync)

        # The pitch value is (2^x/12), where x represents the number of semitones
        pitch = 2 ** (semitones / 12)

        # Apply processing only to the proc_audio stream
        proc_audio = proc_audio.filter("rubberband", pitch=pitch) if is_transposed else proc_audio
        proc_audio = proc_audio.filter("loudnorm", i=-16, tp=-1.5, lra=11) if normalize_audio else proc_audio

        # If original sound requested, scale track 0 (a:0) and mix it with the proc track (a:1)
        if original_sound:
            try:
                vol = float(original_sound_volume) / 100.0
            except Exception:
                vol = 0.5
            scaled_orig = orig_audio.filter("volume", f"{vol}")
            # Use amix to mix processed audio and scaled original (two inputs)
            audio = ffmpeg.filter([proc_audio, scaled_orig], "amix", inputs=2, dropout_transition=0)
            logging.info(f"Including original sound (track 0) at {vol*100}% volume in mix")
        else:
            # No original mixing requested but we reached here because processing was required.
            audio = proc_audio

    # frag_keyframe+default_base_moof is used to set the correct headers for streaming incomplete files,
    # without it, there's better compatibility for streaming on certain browsers like Firefox
    movflags = "+faststart" if buffer_fully_before_playback else "frag_keyframe+default_base_moof"

    if fr.cdg_file_path != None:  # handle CDG files
        logging.info("Playing CDG/MP3 file: " + fr.file_path)
        # copyts helps with sync issues, fps=25 prevents ffmpeg from needlessly encoding cdg at 300fps
        cdg_input = ffmpeg.input(fr.cdg_file_path, copyts=None)
        if cdg_pixel_scaling:
            video = cdg_input.video.filter("fps", fps=25).filter("scale", -1, 720, flags="neighbor")
        else:
            video = cdg_input.video.filter("fps", fps=25)

        # cdg is very fussy about these flags.
        # pi ffmpeg needs to encode to aac and cant just copy the mp3 stream
        # It also appears to have memory issues with hardware acceleration h264_v4l2m2m
        output = ffmpeg.output(
            audio,
            video,
            fr.output_file,
            vcodec="libx264",
            acodec="aac",
            preset="ultrafast",
            pix_fmt="yuv420p",
            listen=1,
            f="mp4",
            video_bitrate="500k",
            movflags=movflags,
        )
    else:
        video = input.video
        output = ffmpeg.output(
            audio,
            video,
            fr.output_file,
            vcodec=vcodec,
            acodec=acodec,
            preset="ultrafast",
            listen=1,
            f="mp4",
            video_bitrate=vbitrate,
            movflags=movflags,
        )

    args = output.get_args()
    logging.info(f"COMMAND: ffmpeg " + " ".join(args))
    return output


def get_ffmpeg_version():
    try:
        # Execute the command 'ffmpeg -version'
        result = subprocess.run(
            ["ffmpeg", "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
        )
        # Parse the first line to get the version
        first_line = result.stdout.split("\n")[0]
        version_info = first_line.split(" ")[2]  # Assumes the version info is the third element
        return version_info
    except FileNotFoundError:
        return "FFmpeg is not installed"
    except IndexError:
        return "Unable to parse FFmpeg version"


def is_transpose_enabled():
    try:
        filters = subprocess.run(["ffmpeg", "-filters"], capture_output=True)
    except FileNotFoundError:
        return False
    except IndexError:
        return False
    return "rubberband" in filters.stdout.decode()


def supports_hardware_h264_encoding():
    try:
        codecs = subprocess.run(["ffmpeg", "-codecs"], capture_output=True)
    except FileNotFoundError:
        return False
    except IndexError:
        return False
    return "h264_v4l2m2m" in codecs.stdout.decode()


def is_ffmpeg_installed():
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True)
    except FileNotFoundError:
        return False
    return True
