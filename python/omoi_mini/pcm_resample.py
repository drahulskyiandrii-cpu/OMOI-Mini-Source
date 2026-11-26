"""Kausaler Anti-Alias-Filter und rationale PCM16-Konvertierung auf 16 kHz.

Nur im Consumer aufrufen, nicht im PortAudio-Callback. Kein AGC/NS/AEC.
"""
import numpy as np

TARGET_RATE=16000
SUPPORTED_RATES=(16000,48000,44100,32000)


class PCMResampler:
    def __init__(self,source_rate):
        if source_rate not in SUPPORTED_RATES:raise ValueError("Unsupported sample rate")
        self.source_rate=source_rate
        self.source_count=0
        self.output_count=0
        if source_rate==TARGET_RATE:return
        self.previous=np.zeros(3,dtype=np.float64)
        # 127-Tap-FIR, 7 kHz Grenzfrequenz, Hamming-Fenster.
        # Kausale Gruppenlaufzeit: 63 Eingangssamples.
        n=np.arange(127,dtype=np.float64)-63
        cutoff=7000/source_rate
        taps=2*cutoff*np.sinc(2*cutoff*n)*np.hamming(127)
        self.taps=taps/taps.sum()
        self.history=np.zeros(126,dtype=np.float64)

    def feed(self,pcm):
        data=np.asarray(pcm,dtype=np.int16).reshape(-1)
        if not len(data):return np.empty(0,dtype="<i2")
        if self.source_rate==TARGET_RATE:
            self.source_count+=len(data)
            self.output_count+=len(data)
            return data.astype("<i2",copy=False)
        source=np.concatenate((self.history,data.astype(np.float64)))
        filtered=np.convolve(source,self.taps,mode="valid")
        self.history=source[-126:].copy()
        end=self.source_count+len(data)
        count=end*TARGET_RATE//self.source_rate
        # Exakte ganzzahlige Zeitbasis: keine Drift durch Chunk-Grenzen.
        positions=np.arange(self.output_count,count,dtype=np.int64)*self.source_rate
        indexes=positions//TARGET_RATE-self.source_count
        fractions=(positions%TARGET_RATE)/TARGET_RATE
        # Bis zu drei vorige Filterwerte fuer verzogerte Ausgabesamples.
        extended=np.concatenate((self.previous,filtered))
        result=np.empty(len(indexes),dtype=np.float64)
        exact=fractions==0
        result[exact]=extended[indexes[exact]+3]
        other=~exact
        result[other]=(extended[indexes[other]+3]*(1-fractions[other])
                       +extended[indexes[other]+4]*fractions[other])
        self.previous=extended[-3:].copy()
        self.source_count=end
        self.output_count=count
        return np.clip(np.rint(result),-32768,32767).astype("<i2")
