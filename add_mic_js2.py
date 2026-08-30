with open('frontend/js/chatbot.js', 'r', errors='replace') as f:
    content = f.read()

# Add microphone hold-to-speak code before the closing IIFE
mic_code = '''

  /* ---------- Microphone (hold-to-speak) ---------- */
  var micBtn = root.querySelector('.mic');
  var micRecognition = null;
  var micActive = false;
  var micSubmitting = false; // Prevent double submission
  var micFinalTranscript = '';
  var micInterimTranscript = '';

  function micSupported() {
    return !!(window.SpeechRecognition || window.webkitSpeechRecognition);
  }

  function micToast(msg, type) { toast(msg, type); }

  function micStart() {
    if (micActive || !micSupported() || state.streaming) return;
    var SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    micRecognition = new SpeechRecognition();
    micRecognition.lang = 'en-IN';
    micRecognition.interimResults = true;
    micRecognition.continuous = true;
    micRecognition.maxAlternatives = 1;

    micFinalTranscript = '';
    micInterimTranscript = '';
    micSubmitting = false;

    micRecognition.onstart = function () {
      micActive = true;
      micBtn.classList.add('listening');
      micBtn.setAttribute('aria-pressed', 'true');
      micBtn.setAttribute('aria-label', 'Release to stop listening');
    };

    micRecognition.onresult = function (event) {
      var interim = '';
      var final = '';
      for (var i = event.resultIndex; i < event.results.length; i++) {
        var transcript = event.results[i][0].transcript;
        if (event.results[i].isFinal) {
          final += transcript + ' ';
        } else {
          interim += transcript;
        }
      }
      if (final) {
        micFinalTranscript += final;
      }
      micInterimTranscript = interim;
      if (interim || final) {
        input.value = (micFinalTranscript + ' ' + micInterimTranscript).trim();
        autosize();
      }
    };

    micRecognition.onerror = function (event) {
      var msg = '';
      switch (event.error) {
        case 'no-speech':
          msg = 'No speech detected. Please try again.';
          break;
        case 'audio-capture':
          msg = 'Microphone not accessible. Check permissions.';
          break;
        case 'not-allowed':
        case 'permission-denied':
          msg = 'Microphone permission denied. Please allow microphone access.';
          break;
        case 'network':
          msg = 'Network error. Please check your connection.';
          break;
        case 'aborted':
          return;
        case 'service-not-allowed':
          msg = 'Speech recognition service not allowed.';
          break;
        case 'bad-grammar':
        case 'language-not-supported':
          msg = 'Language not supported for speech recognition.';
          break;
        default:
          msg = 'Speech recognition error: ' + event.error;
      }
      if (msg) {
        micToast(msg, 'error');
        console.warn('[CUS] Speech recognition error:', event.error);
      }
      micStop();
    };

    micRecognition.onend = function () {
      if (micActive) {
        micFinalize();
      }
    };

    try {
      micRecognition.start();
    } catch (e) {
      micToast('Could not start speech recognition. Please try again.', 'error');
      console.error('[CUS] Speech recognition start error:', e);
    }
  }

  function micFinalize() {
    if (!micActive || micSubmitting) return;
    micActive = false;
    micSubmitting = true;
    if (micRecognition) {
      try { micRecognition.stop(); } catch (e) {}
      micRecognition = null;
    }
    micBtn.classList.remove('listening');
    micBtn.setAttribute('aria-pressed', 'false');
    micBtn.setAttribute('aria-label', 'Hold to speak');

    var transcript = (micFinalTranscript + ' ' + micInterimTranscript).trim();
    micFinalTranscript = '';
    micInterimTranscript = '';

    if (!transcript) {
      micSubmitting = false;
      return;
    }

    input.value = transcript;
    autosize();
    sendClick();
  }

  function micStop() {
    if (!micActive) return;
    if (micRecognition) {
      try { micRecognition.stop(); } catch (e) {}
    }
  }

  if (micBtn) {
    micBtn.addEventListener('pointerdown', function (e) {
      if (state.streaming) return;
      e.preventDefault();
      micBtn.setPointerCapture(e.pointerId);
      micStart();
    });

    micBtn.addEventListener('pointerup', function (e) {
      micStop();
      try { micBtn.releasePointerCapture(e.pointerId); } catch (e) {}
    });

    micBtn.addEventListener('pointercancel', function (e) {
      micStop();
      try { micBtn.releasePointerCapture(e.pointerId); } catch (e) {}
    });

    micBtn.addEventListener('pointerleave', function (e) {
      if (micActive) micStop();
      try { micBtn.releasePointerCapture(e.pointerId); } catch (e) {}
    });

    micBtn.addEventListener('keydown', function (e) {
      if (e.key === ' ' || e.key === 'Enter') {
        e.preventDefault();
        if (!micActive) {
          micStart();
        } else {
          micStop();
        }
      }
    });

    if (!micSupported()) {
      micBtn.disabled = true;
      micBtn.setAttribute('aria-label', 'Voice input not supported in this browser');
      micBtn.title = 'Voice input not supported in this browser';
      micBtn.style.opacity = '.5';
      micBtn.style.cursor = 'not-allowed';
    }
  }
'''

# Insert before the closing IIFE
close_pos = content.rfind('});')
if close_pos >= 0:
    new_content = content[:close_pos] + mic_code + content[close_pos:]
    with open('frontend/js/chatbot.js', 'w', errors='replace') as f:
        f.write(new_content)
    print('Mic JavaScript code added')
else:
    print('Could not find closing IIFE position')