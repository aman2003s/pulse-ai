from kokoro import KPipeline
import soundfile as sf
import os

def generate_earcon():
    print("Generating ack earcon using Kokoro...")
    # Using 'a' for American English
    pipeline = KPipeline(lang_code='a')
    generator = pipeline("Yes?", voice='af_heart', speed=1.2)
    for i, (gs, ps, audio) in enumerate(generator):
        output_path = os.path.join(os.path.dirname(__file__), '..', 'models', 'ack.wav')
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        sf.write(output_path, audio, 24000)
        print(f"Earcon saved to {output_path}")
        break

if __name__ == "__main__":
    generate_earcon()
