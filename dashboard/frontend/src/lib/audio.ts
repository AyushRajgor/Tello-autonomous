export type AudioFrameHandler = (frame: ArrayBuffer) => void;

const TARGET_SAMPLE_RATE = 16000;
const BUFFER_SIZE = 4096;

export function downsampleBuffer(
  buffer: Float32Array,
  inputSampleRate: number,
  outputSampleRate = TARGET_SAMPLE_RATE
): Float32Array {
  if (inputSampleRate === outputSampleRate) {
    return buffer;
  }
  if (inputSampleRate < outputSampleRate) {
    return buffer;
  }

  const ratio = inputSampleRate / outputSampleRate;
  const outputLength = Math.round(buffer.length / ratio);
  const output = new Float32Array(outputLength);

  for (let index = 0; index < outputLength; index += 1) {
    const start = Math.floor(index * ratio);
    const end = Math.min(Math.floor((index + 1) * ratio), buffer.length);
    let sum = 0;
    let samples = 0;

    for (let sourceIndex = start; sourceIndex < end; sourceIndex += 1) {
      sum += buffer[sourceIndex] ?? 0;
      samples += 1;
    }

    output[index] = samples > 0 ? sum / samples : 0;
  }

  return output;
}

export function floatTo16BitPcm(buffer: Float32Array): Int16Array {
  const output = new Int16Array(buffer.length);

  for (let index = 0; index < buffer.length; index += 1) {
    const sample = Math.max(-1, Math.min(1, buffer[index] ?? 0));
    output[index] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
  }

  return output;
}

export class PushToTalkAudio {
  private audioContext: AudioContext | undefined;
  private mediaStream: MediaStream | undefined;
  private source: MediaStreamAudioSourceNode | undefined;
  private processor: ScriptProcessorNode | undefined;

  async start(onFrame: AudioFrameHandler) {
    if (this.audioContext) {
      return;
    }

    const stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        noiseSuppression: true,
        echoCancellation: true,
        autoGainControl: true
      }
    });
    const AudioContextCtor = window.AudioContext || window.webkitAudioContext;
    const context = new AudioContextCtor();
    const source = context.createMediaStreamSource(stream);
    const processor = context.createScriptProcessor(BUFFER_SIZE, 1, 1);

    processor.onaudioprocess = (event) => {
      const input = event.inputBuffer.getChannelData(0);
      const downsampled = downsampleBuffer(input, context.sampleRate, TARGET_SAMPLE_RATE);
      const pcm = floatTo16BitPcm(downsampled);
      const frame = new ArrayBuffer(pcm.byteLength);
      new Int16Array(frame).set(pcm);
      onFrame(frame);
    };

    source.connect(processor);
    processor.connect(context.destination);

    this.mediaStream = stream;
    this.audioContext = context;
    this.source = source;
    this.processor = processor;
  }

  async stop() {
    this.processor?.disconnect();
    this.source?.disconnect();
    this.mediaStream?.getTracks().forEach((track) => track.stop());

    if (this.audioContext && this.audioContext.state !== "closed") {
      await this.audioContext.close();
    }

    this.audioContext = undefined;
    this.mediaStream = undefined;
    this.source = undefined;
    this.processor = undefined;
  }
}

declare global {
  interface Window {
    webkitAudioContext?: typeof AudioContext;
  }
}
