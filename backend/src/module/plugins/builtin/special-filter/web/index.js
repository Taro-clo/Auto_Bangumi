// 设置页分区：编辑特典文件名规则（一行一条正则），走本插件的 GET/POST /rules。
// 无需构建：宿主在包裹元素的 Shadow DOM 中注入 --ab-* 主题变量。
const TEXT = {
  'zh-CN': {
    title: '特典文件名规则',
    hint: '一行一条 Python 正则（不区分大小写）。命中文件名的正片/字幕会被忽略，保留原名、不参与改名。空行自动跳过；保存时逐条校验，写错会提示。',
    placeholder: 'ncop|nced|creditless',
    save: '保存规则',
    saving: '保存中…',
    reset: '恢复默认',
    saved: '已保存 {n} 条规则',
    loaded: '当前 {n} 条规则',
    invalid: '保存失败：{detail}',
    loadFailed: '读取规则失败，详见日志',
    dirty: '有未保存的修改',
  },
  'en-US': {
    title: 'Special file name rules',
    hint: 'One Python regex per line (case-insensitive). Videos/subtitles whose names match are ignored: they keep their original names and are not renamed. Empty lines are skipped; each pattern is validated on save.',
    placeholder: 'ncop|nced|creditless',
    save: 'Save rules',
    saving: 'Saving…',
    reset: 'Restore defaults',
    saved: 'Saved {n} rules',
    loaded: '{n} rules',
    invalid: 'Save failed: {detail}',
    loadFailed: 'Failed to read rules. See the log.',
    dirty: 'Unsaved changes',
  },
};

class SpecialFilterRules extends HTMLElement {
  connectedCallback() {
    const text = TEXT[this.host.i18n.locale] ?? TEXT['en-US'];
    this.text = text;
    const root = this.shadowRoot ?? this.attachShadow({ mode: 'open' });
    root.innerHTML = `
      <style>
        :host { display: block; }
        section {
          display: flex; flex-direction: column; gap: 10px;
          padding: 16px; background: var(--ab-color-surface);
          border: 1px solid var(--ab-color-border); border-radius: var(--ab-radius-md);
        }
        h3 { margin: 0; font-size: 14px; font-weight: 600; }
        p { margin: 0; font-size: 12px; line-height: 1.6; color: var(--ab-color-text-secondary); }
        textarea {
          width: 100%; box-sizing: border-box; min-height: 220px; resize: vertical;
          padding: 10px 12px; font-family: var(--ab-font-mono); font-size: 12px; line-height: 1.7;
          color: var(--ab-color-text); background: var(--ab-color-surface-2);
          border: 1px solid var(--ab-color-border); border-radius: var(--ab-radius-sm);
        }
        textarea:focus-visible { outline: 2px solid var(--ab-color-primary); outline-offset: 1px; }
        .actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
        button {
          padding: 6px 12px; border: none; border-radius: var(--ab-radius-sm);
          background: var(--ab-color-surface-2); color: var(--ab-color-text);
          font: inherit; font-size: 13px; cursor: pointer;
        }
        button.primary { background: var(--ab-color-primary); color: #fff; }
        button:hover { filter: brightness(1.05); }
        button:focus-visible { outline: 2px solid var(--ab-color-primary); outline-offset: 2px; }
        button:disabled { opacity: 0.5; cursor: default; }
        output { font-size: 12px; color: var(--ab-color-text-secondary); }
        output[data-kind="error"] { color: var(--ab-color-danger, #d03050); }
      </style>
      <section>
        <h3></h3>
        <p class="hint"></p>
        <textarea spellcheck="false"></textarea>
        <div class="actions">
          <button type="button" class="primary save"></button>
          <button type="button" class="reset"></button>
          <output aria-live="polite"></output>
        </div>
      </section>
    `;
    root.querySelector('h3').textContent = text.title;
    root.querySelector('.hint').textContent = text.hint;
    this.textarea = root.querySelector('textarea');
    this.textarea.placeholder = text.placeholder;
    this.output = root.querySelector('output');
    this.saveButton = root.querySelector('.save');
    this.saveButton.textContent = text.save;
    this.resetButton = root.querySelector('.reset');
    this.resetButton.textContent = text.reset;

    this.defaults = [];
    this.textarea.addEventListener('input', () => {
      this.output.dataset.kind = '';
      this.output.textContent = text.dirty;
    });
    this.saveButton.addEventListener('click', () => this.save());
    this.resetButton.addEventListener('click', async () => {
      if (!this.defaults.length) return;
      this.textarea.value = this.defaults.join('\n');
      await this.save();
    });
    this.load();
  }

  status(message, kind = '') {
    this.output.dataset.kind = kind;
    this.output.textContent = message;
  }

  lines() {
    return this.textarea.value
      .split('\n')
      .map((line) => line.trim())
      .filter(Boolean);
  }

  async load() {
    try {
      const data = await this.host.api.get('rules');
      this.defaults = data.defaults ?? [];
      this.textarea.value = (data.rules ?? []).join('\n');
      this.status(this.text.loaded.replace('{n}', String((data.rules ?? []).length)));
    } catch {
      this.status(this.text.loadFailed, 'error');
    }
  }

  async save() {
    this.saveButton.disabled = true;
    this.saveButton.textContent = this.text.saving;
    try {
      const data = await this.host.api.post('rules', { rules: this.lines() });
      const count = (data.rules ?? []).length;
      this.status(this.text.saved.replace('{n}', String(count)));
      this.host.toast(this.text.saved.replace('{n}', String(count)));
    } catch (error) {
      const detail = error?.response?.data?.detail ?? error?.message ?? '';
      this.status(this.text.invalid.replace('{detail}', String(detail)), 'error');
    } finally {
      this.saveButton.disabled = false;
      this.saveButton.textContent = this.text.save;
    }
  }
}

if (!customElements.get('ab-plugin-special-filter-rules')) {
  customElements.define('ab-plugin-special-filter-rules', SpecialFilterRules);
}
