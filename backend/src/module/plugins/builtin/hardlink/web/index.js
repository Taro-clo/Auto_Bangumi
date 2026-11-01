// 设置页分区：把下载根目录下已有的文件补链到媒体库（调用本插件的 POST /backfill）。
// 无需构建：宿主在包裹元素的 Shadow DOM 中注入 --ab-* 主题变量，这里直接使用。
const TEXT = {
  'zh-CN': {
    title: '链接已有文件',
    hint: '把下载根目录下已有的正片与字幕链接到媒体库。可以只处理某个作品或某一季，也可以处理整棵下载根目录。只在点击时运行。',
    scope: '范围',
    all: '全部（整棵下载根目录）',
    button: '开始链接',
    running: '链接中…',
    done: '已链接 {linked} · 已存在 {exists} · 冲突 {conflict} · 失败 {failed}',
    failed: '补链失败，详见日志',
    noDirs: '下载根目录下没有可选目录',
  },
  'en-US': {
    title: 'Link existing files',
    hint: 'Link the videos and subtitles already in the download root into the library. You can limit the run to one title or one season, or process the whole download root. Runs only when clicked.',
    scope: 'Scope',
    all: 'Everything (whole download root)',
    button: 'Start linking',
    running: 'Linking…',
    done: 'linked {linked} · existing {exists} · conflict {conflict} · failed {failed}',
    failed: 'Backfill failed. See the log.',
    noDirs: 'No directories under the download root',
  },
};

class HardlinkBackfill extends HTMLElement {
  connectedCallback() {
    const text = TEXT[this.host.i18n.locale] ?? TEXT['en-US'];
    const root = this.shadowRoot ?? this.attachShadow({ mode: 'open' });
    root.innerHTML = `
      <style>
        :host { display: block; }
        section {
          display: flex; flex-direction: column; align-items: flex-start; gap: 8px;
          padding: 16px; background: var(--ab-color-surface);
          border: 1px solid var(--ab-color-border); border-radius: var(--ab-radius-md);
        }
        h3 { margin: 0; font-size: 14px; font-weight: 600; }
        p { margin: 0; font-size: 12px; color: var(--ab-color-text-secondary); }
        label { display: flex; flex-direction: column; gap: 4px; font-size: 12px;
          color: var(--ab-color-text-secondary); width: 100%; max-width: 420px; }
        select {
          width: 100%; padding: 6px 8px; font: inherit; font-size: 13px;
          color: var(--ab-color-text); background: var(--ab-color-surface-2);
          border: 1px solid var(--ab-color-border); border-radius: var(--ab-radius-sm);
        }
        button {
          padding: 6px 12px; border: none; border-radius: var(--ab-radius-sm);
          background: var(--ab-color-surface-2); color: var(--ab-color-text);
          font: inherit; font-size: 13px; cursor: pointer;
        }
        button:hover { background: var(--ab-color-surface-hover); }
        button:focus-visible { outline: 2px solid var(--ab-color-primary); outline-offset: 2px; }
        button:disabled { opacity: 0.5; cursor: default; }
        output { font-family: var(--ab-font-mono); font-size: 12px; font-variant-numeric: tabular-nums; }
      </style>
      <section>
        <h3></h3>
        <p></p>
        <label><span></span><select></select></label>
        <button type="button"></button>
        <output aria-live="polite"></output>
      </section>
    `;
    const [title, hint] = [root.querySelector('h3'), root.querySelector('p')];
    const label = root.querySelector('label > span');
    const select = root.querySelector('select');
    const button = root.querySelector('button');
    const output = root.querySelector('output');
    title.textContent = text.title;
    hint.textContent = text.hint;
    label.textContent = text.scope;
    button.textContent = text.button;

    const fill = (dirs) => {
      select.replaceChildren();
      const all = document.createElement('option');
      all.value = '';
      all.textContent = text.all;
      select.append(all);
      for (const dir of dirs) {
        const option = document.createElement('option');
        option.value = dir;
        option.textContent = dir;
        select.append(option);
      }
      if (dirs.length === 0) {
        hint.textContent = `${text.hint} ${text.noDirs}`;
      }
    };

    fill([]);
    this.host.api
      .get('dirs')
      .then((payload) => fill(Array.isArray(payload?.dirs) ? payload.dirs : []))
      .catch(() => fill([]));

    button.addEventListener('click', async () => {
      button.disabled = true;
      select.disabled = true;
      button.textContent = text.running;
      output.textContent = '';
      try {
        const counts = await this.host.api.post('backfill', { path: select.value || null });
        output.textContent = text.done.replace(/\{(\w+)\}/g, (_, key) => counts[key] ?? 0);
      } catch {
        this.host.toast(text.failed, 'error');
      } finally {
        button.disabled = false;
        select.disabled = false;
        button.textContent = text.button;
      }
    });
  }
}

if (!customElements.get('ab-plugin-hardlink-backfill')) {
  customElements.define('ab-plugin-hardlink-backfill', HardlinkBackfill);
}
