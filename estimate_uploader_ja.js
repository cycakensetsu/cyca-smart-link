// Translate the app's existing uploader, keeping its native input and handlers.
(() => {
  const selector = '.st-key-beginner_intake [data-testid="stFileUploaderDropzone"]';
  const browse = 'ファイルを選ぶ';
  const installed = Symbol.for('cyca.estimateUploaderJapanese');
  if (document[installed]) {
    document[installed]();
    return;
  }

  function textNodes(element, translate) {
    if (!element) return;
    const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
    let text;
    while ((text = walker.nextNode())) {
      const translated = translate(text.nodeValue);
      if (translated !== text.nodeValue) text.nodeValue = translated;
    }
  }

  function apply() {
    for (const dropzone of document.querySelectorAll(selector)) {
      const button = dropzone.querySelector('button');
      if (button && (button.textContent.trim() === 'Browse files' || button.textContent.trim() === browse)) {
        textNodes(button, text => text === 'Browse files' ? browse : text);
        button.setAttribute('aria-label', browse);
        button.setAttribute('title', browse);
      }
      const instructions = dropzone.querySelector('[data-testid="stFileUploaderDropzoneInstructions"]');
      textNodes(instructions, text => {
        // Streamlit renders these phrases as either one or several text nodes.
        if (/^Drag and drop files? here$/.test(text)) return 'ここにファイルをドロップ';
        if (text === 'Drag and drop ') return 'ここに';
        if (/^files?$/.test(text)) return 'ファイルを';
        if (text === ' here') return 'ドロップ';
        const limit = /^Limit\s+([0-9.,]+\s*[A-Za-z]+) per file(.*)$/.exec(text);
        return limit ? '1ファイル ' + limit[1] + 'まで' + limit[2] : text;
      });
    }
  }

  document[installed] = apply;
  const observer = new MutationObserver(apply);
  // Watch native widget mounts/re-renders, not file values or user events.
  observer.observe(document.body, {childList: true, subtree: true, characterData: true});
  apply();
})();
