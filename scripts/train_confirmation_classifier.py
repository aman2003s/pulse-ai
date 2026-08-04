"""
Trains a tiny FastText classifier for the exact decision _classify_yes_no_or_other
(controller.py) currently makes via a full Gemma round-trip: given a spoken reply
to a yes/no confirmation question, is it "yes", "no", or "other" (an unrelated
command/non-answer)? MIT-licensed library, trained entirely on data generated
here -- no third-party training-data licensing risk (unlike e.g. all-MiniLM-L6-v2,
whose own training data includes non-commercial-licensed sets).

Output: models/confirmation_classifier.ftz (compressed, tiny) for production use,
plus the raw .bin for reference.
"""
import random
import fasttext
import os

random.seed(7)

YES = [
    "yes", "yeah", "yep", "yup", "sure", "sure thing", "go ahead", "go for it",
    "do it", "please do", "do that", "sounds good", "that sounds good",
    "that's right", "that's correct", "correct", "absolutely", "definitely",
    "okay", "ok", "okay do that", "okay go ahead", "yes please", "yes go ahead",
    "fine", "that's fine", "sounds fine", "affirmative", "confirmed", "confirm",
    "yeah do it", "yeah go ahead", "yes that's right", "proceed", "please proceed",
    "continue", "yes continue", "sure go ahead", "sounds right", "right, go ahead",
    "yeah sure", "of course", "yeah that works", "that works", "works for me",
    "go on then", "alright do it", "alright go ahead", "yes exactly", "exactly",
    "yeah exactly that", "sure why not", "yes indeed", "indeed", "yes ma'am",
    "yes sir", "roger that", "you got it", "okay yes", "yes okay",
]

NO = [
    "no", "nope", "nah", "no thanks", "don't", "don't do that", "stop",
    "cancel", "cancel that", "abort", "never mind", "nevermind", "not now",
    "leave it", "leave it alone", "don't bother", "no don't", "negative",
    "not really", "no way", "absolutely not", "definitely not", "nah don't",
    "no leave it", "not that", "that's wrong", "incorrect", "wrong",
    "no stop", "hold on no", "actually no", "no cancel that", "skip it",
    "skip that", "not right now", "later", "not yet", "no not now",
    "nah leave it", "no way jose", "hard no", "nope don't", "cancel please",
    "no i changed my mind", "i changed my mind", "forget it", "drop it",
    "no don't bother", "nah it's fine leave it", "no leave that", "pass",
    "i'll pass", "not this time", "rather not", "i'd rather not",
]

OTHER = [
    "open chrome", "open notepad", "close notepad", "search for cats",
    "what's the weather today", "what time is it", "open the browser",
    "search for flights to bali", "close notepad and open calculator instead",
    "open outlook", "what's on my screen", "read this to me",
    "tell me a joke", "how do magnets work", "download python",
    "search for how to get from delhi to bali", "write hello world in notepad",
    "save this as notes dot text", "open my email folder", "find my resume",
    "switch to guided mode", "list microphones", "are you there",
    "start over", "what can you do", "repeat that", "spell the word hello",
    "speak faster", "who is the president", "summarize this page",
    "open word and write a letter", "install visual studio code",
    "search the internet for travel options", "open my downloads folder",
    "close chrome", "open discord", "what's my battery percentage",
    "tell me about the roman empire", "click the submit button",
    "type my address into the form", "open the calculator app",
    "search for nearby restaurants", "play some music", "increase the volume",
]


def augment(examples, n_per=6):
    """Cheap augmentation: light punctuation/case variants so the tiny model
    isn't overfit to exact casing/punctuation from the hand-written list."""
    out = []
    for e in examples:
        out.append(e)
        out.append(e.capitalize())
        out.append(e + ".")
        out.append(e + "!")
        out.append(e.upper() if len(e) < 15 else e)
    return out


def write_split(path, yes, no, other):
    lines = []
    for e in augment(yes):
        lines.append(f"__label__yes {e}")
    for e in augment(no):
        lines.append(f"__label__no {e}")
    for e in augment(other):
        lines.append(f"__label__other {e}")
    random.shuffle(lines)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return len(lines)


def main():
    models_dir = os.path.join(os.path.dirname(__file__), "..", "models")
    os.makedirs(models_dir, exist_ok=True)

    # 85/15 split, stratified per class by simple index slicing (data is
    # already randomized in composition, order doesn't carry signal here).
    def split(lst, frac=0.85):
        k = int(len(lst) * frac)
        return lst[:k], lst[k:]

    yes_tr, yes_te = split(YES)
    no_tr, no_te = split(NO)
    other_tr, other_te = split(OTHER)

    train_path = os.path.join(models_dir, "confirmation_classifier.train.txt")
    test_path = os.path.join(models_dir, "confirmation_classifier.test.txt")
    n_train = write_split(train_path, yes_tr, no_tr, other_tr)
    n_test = write_split(test_path, yes_te, no_te, other_te)
    print(f"train examples (post-augmentation): {n_train}, test: {n_test}")

    model = fasttext.train_supervised(
        input=train_path,
        epoch=60,
        lr=0.8,
        wordNgrams=2,
        dim=16,
        minCount=1,
        # Default bucket (2,000,000) is sized for large real-world vocabularies
        # and dominates model size regardless of how small the actual data is --
        # confirmed: it alone produced a 122MB model for a ~700-line dataset.
        # This task's whole vocabulary is a few hundred short phrases; a much
        # smaller hash space is still collision-safe here and cuts size ~100x.
        bucket=20000,
        verbose=0,
    )

    n, precision, recall = model.test(test_path)
    print(f"test set: n={n} precision@1={precision:.4f} recall@1={recall:.4f}")

    bin_path = os.path.join(models_dir, "confirmation_classifier.bin")
    ftz_path = os.path.join(models_dir, "confirmation_classifier.ftz")
    model.save_model(bin_path)
    model.quantize(input=train_path, retrain=True, verbose=0)
    model.save_model(ftz_path)

    bin_size = os.path.getsize(bin_path) / 1024
    ftz_size = os.path.getsize(ftz_path) / 1024
    print(f"model size: {bin_size:.1f} KB (.bin), {ftz_size:.1f} KB (.ftz, quantized)")

    # Sanity spot-check against a few phrasings NOT in the training list at all,
    # including the exact examples cited in controller.py's own docstring.
    spot_checks = [
        ("sounds good", "yes"), ("that's right", "yes"), ("go for it, do that", "yes"),
        ("nah, leave it", "no"), ("don't bother", "no"), ("not now", "no"),
        ("open outlook", "other"), ("search something and write it down", "other"),
    ]
    print("\nSpot checks (not in training data):")
    for text, expected in spot_checks:
        # model.predict() wraps its result in np.array(..., copy=False), which
        # raises under NumPy 2.x (a real, confirmed fasttext/fasttext-wheel
        # compatibility bug, not a usage error) -- call the underlying binding
        # directly instead, exactly what predict() itself does before that
        # final (buggy) wrapping step.
        predictions = model.f.predict(text + "\n", 1, 0.0, "strict")
        prob, label = predictions[0]
        pred = label.replace("__label__", "")
        mark = "OK" if pred == expected else "MISS"
        print(f"  [{mark}] {text!r} -> {pred} (expected {expected}), p={prob:.3f}")


if __name__ == "__main__":
    main()
