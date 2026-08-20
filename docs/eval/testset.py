"""Labelled retake eval set. Lives in the REPO, not /tmp -- the previous version was lost.

Each script is (id, language, lines, must_cut). `must_cut` is 1-based line numbers that a correct
detector removes; EVERY other line must survive. Decoys are placed deliberately adjacent to real
retakes so a detector cannot pass by cutting anything that looks repeated.

Decoy classes represented: rhetorical repetition, emphasis doubling, shared-stem list, callback,
refrain, bookend (intro/outro catchphrase), contrast pair, question-then-answer, quotation,
keyword echo, teaching recap, and -- new in this version -- BILINGUAL GLOSS, the predicted top
false-positive class for Hinglish and the one class the English-only set could not test.
"""

SCRIPTS = [
# ---------------------------------------------------------------- English
("en-hallucination", "en", [
  "AI will give you a completely wrong answer and sound sure about it.",
  "This is called hallucination.",
  "It just,",                                        # 3 fragment
  "it just makes things up.",
  "Fake quotes, fake statistics, fake research.",    # 5 shared-stem list -- KEEP
  "All delivered in the same tone.",                 # 6 attempt
  "All delivered in the same confident tone.",
  "So it's not lying on purpose.",                   # 8 verbatim repeat
  "So it's not lying on purpose.",
  "It genuinely doesn't know the difference between true and sounds true.",
], {3, 6, 8}),

("en-intern", "en", [
  "The smartest way to use AI isn't to trust it blindly.",
  "It's to treat it...",                             # 2 fragment
  "It's to treat it like a smart intern.",
  "Fast, useful.",                                   # 4 verbatim repeat
  "Fast, useful.",
  "But you still check the important stuff.",
  "Check the important stuff.",                      # 7 rhetorical echo -- KEEP
  "Comment below and I'll send you three things.",
], {2, 4}),

("en-mcp-bookend", "en", [
  "One connector that links any AI to any app.",      # 1 bookend intro -- KEEP
  "Before MCP, every connection was hand-built.",
  "Want your AI to read your drive, hit a database, and post to Slack?",  # 3 Q -- KEEP
  "That's three different builds.",                   # 4 A -- KEEP
  "I've wired these up myself, but it's slow.",
  "It breaks constantly.",
  "MCP gives one sh--",                               # 7 truncated
  "MCP gives them one single shared language.",
  "One connector that links any AI to any app.",       # 9 bookend outro -- KEEP
], {7}),

("en-correction", "en", [
  "This feature launched in 2019.",                   # 1 wrong value
  "Sorry, 2018. This feature launched in 2018.",
  "Ten thousand connectors are live right now.",
  "Ten thousand.",                                    # 4 emphasis echo -- KEEP
  "As Sam Altman put it, the models are getting cheaper.",  # 5 quotation -- KEEP
  "And cheaper means everyone can build.",
], {1}),

# ---------------------------------------------------------------- Hinglish
("hi-en-gloss", "hinglish", [
  "Aaj hum baat karenge React hooks ke baare mein.",
  "Yeh cheez bahut zaroori hai.",                     # 2 GLOSS pair -- KEEP
  "This is really important, guys.",                  # 3 GLOSS pair -- KEEP
  "Hooks sirf functional components mein--",           # 4 truncated
  "Hooks sirf functional components mein kaam karte hain.",
  "Bilkul bilkul, yahi baat hai.",                    # 6 emphasis doubling -- KEEP
], {4}),

("hi-en-marker", "hinglish", [
  "Iska sabse bada faayda ye hai ki aap--",            # 1 fragment
  "Ruko, phir se bolta hoon.",                         # 2 marker line, belongs to discard
  "Iska sabse bada faayda ye hai ki aap performance improve kar sakte hain.",
  "Pehla point: speed.",                               # 4 list -- KEEP
  "Doosra point: memory.",                             # 5 list -- KEEP
  "Teesra point: cost.",                               # 6 list -- KEEP
  "Jaise maine pehle bataya tha, hooks sirf functional components mein chalte hain.",  # 7 recap -- KEEP
], {1, 2}),

("hi-en-number", "hinglish", [
  "Yeh feature 2019 mein launch hua tha.",             # 1 wrong value
  "Nahi nahi, 2018 mein launch hua tha.",
  "useEffect.",                                        # 3 keyword echo -- KEEP
  "useEffect ek aisa hook hai jo side effects handle karta hai.",  # 4 -- KEEP
  "Toh matlab basically yeh simple hai.",              # 5 fillers only, not a marker -- KEEP
], {1}),

("hi-en-chain", "hinglish", [
  "Toh aaj hum--",                                     # 1
  "Toh aaj hum baat--",                                # 2
  "Toh aaj hum baat karenge deployment ke baare mein.",
  "Deployment matlab apna code live karna.",           # 4 gloss-ish definition -- KEEP
  "Samjhe? Chalo aage badhte hain.",                   # 5 -- KEEP
], {1, 2}),

# ---------------------------------------------------------------- Hindi
("hi-restart", "hi", [
  "आज हम बात करेंगे इस नए फीचर के बारे में।",
  "यह फीचर आपको--",                                    # 2 fragment
  "एक मिनट, फिर से।",                                   # 3 marker, belongs to discard
  "यह फीचर आपको समय बचाने में मदद करता है।",
  "समय बचाना बहुत ज़रूरी है।",                          # 5 -- KEEP
], {2, 3}),

("hi-rhetoric", "hi", [
  "यह गलत है।",                                        # 1 rhetorical repetition -- KEEP
  "यह बिलकुल गलत है।",                                  # 2 -- KEEP
  "इसलिए हमें ध्यान रखना चाहिए।",
  "इसलिए हमें ध्यान रखना चाहिए।",                        # 4 verbatim repeat -- CUT
  "अब अगला पॉइंट देखिए।",
], {3}),

("hi-quote", "hi", [
  "उन्होंने कहा, यह तकनीक सबके लिए है।",                 # 1 quotation -- KEEP
  "और सबके लिए मतलब हर छोटे बिज़नेस के लिए।",
  "माफ़ कीजिए, हर बिज़नेस के लिए नहीं, हर डेवलपर के लिए।",   # 3 correction of 2
  "तो यही मुख्य बात है।",
], {2}),

("hi-none", "hi", [
  "नमस्ते दोस्तों, स्वागत है।",
  "आज तीन चीज़ें सीखेंगे।",
  "पहली: सेटअप।",
  "दूसरी: कोड।",
  "तीसरी: डिप्लॉय।",
  "चलिए शुरू करते हैं।",
], set()),   # control: nothing should be cut
]

def stats():
    n = sum(len(s[2]) for s in SCRIPTS)
    c = sum(len(s[3]) for s in SCRIPTS)
    langs = {}
    for _, lg, ls, cs in SCRIPTS:
        langs.setdefault(lg, [0, 0])
        langs[lg][0] += len(ls); langs[lg][1] += len(cs)
    return n, c, langs

if __name__ == "__main__":
    n, c, langs = stats()
    print(f"{len(SCRIPTS)} scripts, {n} lines, {c} must-cut, {n-c} must-keep")
    for lg, (l, cc) in sorted(langs.items()):
        print(f"  {lg:9} {l:3d} lines, {cc:2d} must-cut")
