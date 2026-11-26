"""Striktes PCM-WAV-Format fuer lokale TTS und Wiedergabe."""
import io
import wave


def read_pcm_wav(source,*,max_bytes=32*1024*1024,channels=(1,2)):
    try:
        with wave.open(source,"rb") as wav:
            rate=wav.getframerate()
            count=wav.getnchannels()
            frames=wav.getnframes()
            size=frames*count*2
            if (wav.getcomptype()!="NONE" or wav.getsampwidth()!=2
                    or count not in channels or not 8000<=rate<=192000
                    or not 0<size<=max_bytes):
                raise ValueError("Invalid PCM WAV format")
            data=wav.readframes(frames)
            if len(data)!=size:raise ValueError("Truncated PCM WAV")
            return rate,count,data
    except RuntimeError:
        # wave._Chunk.seek kann bei kaputten RIFF-Groessen RuntimeError werfen.
        raise ValueError("Invalid WAV chunk structure") from None


def validate_pcm_wav(data,*,max_bytes=32*1024*1024,channels=(1,)):
    if not isinstance(data,bytes) or not 44<=len(data)<=max_bytes:
        raise ValueError("Invalid PCM WAV size")
    read_pcm_wav(io.BytesIO(data),max_bytes=max_bytes,channels=channels)
