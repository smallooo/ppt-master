// 展示工具：字节、时间、状态文案、source/confirmation 装饰器
function fmtBytes(n) {
  if (!n && n !== 0) return '-';
  n = Number(n);
  if (n < 1024) return n + ' B';
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB';
  if (n < 1024 * 1024 * 1024) return (n / 1024 / 1024).toFixed(1) + ' MB';
  return (n / 1024 / 1024 / 1024).toFixed(2) + ' GB';
}

function fmtTime(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  const diff = (Date.now() - d.getTime()) / 1000;
  if (diff < 60) return '刚刚';
  if (diff < 3600) return Math.floor(diff / 60) + ' 分钟前';
  if (diff < 86400) return Math.floor(diff / 3600) + ' 小时前';
  if (diff < 86400 * 7) return Math.floor(diff / 86400) + ' 天前';
  const pad = n => (n < 10 ? '0' + n : '' + n);
  return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate())
       + ' ' + pad(d.getHours()) + ':' + pad(d.getMinutes());
}

const PROJECT_STATUS = {
  created:               { text: '已创建',   cls: 'badge',      step: 1 },
  uploading:             { text: '上传中',   cls: 'badge',      step: 1 },
  normalizing:           { text: '解析中',   cls: 'badge-warn', step: 1 },
  awaiting_confirmation: { text: '可生成',   cls: 'badge-warn', step: 3 },
  ready_to_generate:     { text: '待生成',   cls: 'badge-warn', step: 3 },
  generating:            { text: '生成中',   cls: 'badge-warn', step: 3 },
  post_processing:       { text: '后处理中', cls: 'badge-warn', step: 3 },
  exporting:             { text: '导出中',   cls: 'badge-warn', step: 3 },
  completed:             { text: '已完成',   cls: 'badge-ok',   step: 4 },
  failed:                { text: '失败',     cls: 'badge-err',  step: 0 },
  cancelled:             { text: '已取消',   cls: 'badge-err',  step: 0 }
};

const ACTIVE_STATUSES = [
  'created', 'uploading', 'normalizing', 'awaiting_confirmation',
  'ready_to_generate', 'generating', 'post_processing', 'exporting'
];
const DONE_STATUSES = ['completed'];
const FAILED_STATUSES = ['failed', 'cancelled'];

const JOB_STATUS = {
  queued:    { text: '排队中',  cls: 'badge' },
  running:   { text: '生成中',  cls: 'badge-warn' },
  retrying:  { text: '重试中',  cls: 'badge-warn' },
  succeeded: { text: '成功',    cls: 'badge-ok' },
  failed:    { text: '失败',    cls: 'badge-err' },
  cancelled: { text: '已取消',  cls: 'badge-err' }
};

const SOURCE_STATUS = {
  uploaded:    { text: '已上传', cls: 'badge' },
  normalizing: { text: '解析中', cls: 'badge-warn' },
  ready:       { text: '已就绪', cls: 'badge-ok' },
  failed:      { text: '失败',   cls: 'badge-err' }
};

const CONFIRMATION_STATUS = {
  pending:  { text: '待确认', cls: 'badge-warn' },
  approved: { text: '已确认', cls: 'badge-ok' },
  revised:  { text: '已回退', cls: 'badge-err' }
};

const SOURCE_KIND = {
  pdf:      { label: 'PDF',       emoji: '📕' },
  docx:     { label: 'Word',      emoji: '📘' },
  pptx:     { label: 'PPT',       emoji: '📊' },
  xlsx:     { label: 'Excel',     emoji: '📗' },
  markdown: { label: 'Markdown',  emoji: '📝' },
  html:     { label: '网页',      emoji: '🌐' },
  epub:     { label: 'EPUB',      emoji: '📚' },
  other:    { label: '其他',      emoji: '📎' }
};

function decorateProject(p) {
  if (!p) return p;
  const meta = PROJECT_STATUS[p.status] || { text: p.status || '未知', cls: 'badge', step: 0 };
  return Object.assign({}, p, {
    _statusText: meta.text,
    _statusCls: meta.cls,
    _step: meta.step,
    _updatedText: fmtTime(p.updated_at),
    _createdText: fmtTime(p.created_at)
  });
}

function decorateJob(j) {
  if (!j) return j;
  const meta = JOB_STATUS[j.status] || { text: j.status || '未知', cls: 'badge' };
  const pct = (j.progress_percent === null || j.progress_percent === undefined) ? 0 : Number(j.progress_percent);
  return Object.assign({}, j, {
    _statusText: meta.text,
    _statusCls: meta.cls,
    _isActive: j.status === 'queued' || j.status === 'running' || j.status === 'retrying',
    _isFinal: j.status === 'succeeded' || j.status === 'failed' || j.status === 'cancelled',
    _updatedText: fmtTime(j.updated_at),
    _progressPct: Math.max(0, Math.min(100, Math.round(pct)))
  });
}

function decorateSource(s) {
  if (!s) return s;
  const k = SOURCE_KIND[s.source_kind] || SOURCE_KIND.other;
  const meta = SOURCE_STATUS[s.status] || { text: s.status || '处理中', cls: 'badge' };
  return Object.assign({}, s, {
    _kindLabel: k.label,
    _kindEmoji: k.emoji,
    _statusText: meta.text,
    _statusCls: meta.cls,
    _sizeText: fmtBytes(s.size_bytes)
  });
}

function decorateConfirmation(c) {
  if (!c) return c;
  const meta = CONFIRMATION_STATUS[c.status] || { text: c.status || '待确认', cls: 'badge' };
  return Object.assign({}, c, {
    _statusText: meta.text,
    _statusCls: meta.cls,
    _approvedText: fmtTime(c.approved_at)
  });
}

function decorateEvent(e) {
  if (!e) return e;
  const pct = (e.progress_percent === null || e.progress_percent === undefined)
    ? '' : Math.round(Number(e.progress_percent)) + '%';
  return Object.assign({}, e, {
    _pctText: pct,
    _timeText: fmtTime(e.created_at || e.timestamp)
  });
}

// 把 suggested_spec 拆成可读的 key/value 数组，便于 wxml 列表渲染
function specEntries(spec) {
  if (!spec || typeof spec !== 'object') return [];
  const labelMap = {
    title: '主题',
    subtitle: '副标题',
    audience: '受众',
    purpose: '目的',
    tone: '风格',
    style: '风格',
    page_count: '页数',
    page_min: '最少页数',
    page_max: '最多页数',
    canvas_format: '画布',
    template: '模板',
    theme: '主题',
    color_palette: '配色',
    sections: '章节',
    outline: '大纲',
    keywords: '关键词',
    language: '语言'
  };
  const out = [];
  Object.keys(spec).forEach(function (k) {
    const v = spec[k];
    let text;
    if (v === null || v === undefined) text = '—';
    else if (Array.isArray(v)) text = v.join('、');
    else if (typeof v === 'object') text = JSON.stringify(v);
    else text = String(v);
    out.push({ key: k, label: labelMap[k] || k, value: text });
  });
  return out;
}

function inferSourceKind(filename) {
  const ext = ((filename || '').match(/\.[^.]+$/) || [''])[0].toLowerCase();
  if (ext === '.pdf') return 'pdf';
  if (ext === '.docx' || ext === '.doc') return 'docx';
  if (ext === '.pptx' || ext === '.ppt') return 'pptx';
  if (ext === '.xlsx' || ext === '.xls' || ext === '.xlsm') return 'xlsx';
  if (ext === '.md' || ext === '.markdown' || ext === '.txt') return 'markdown';
  if (ext === '.html' || ext === '.htm') return 'html';
  if (ext === '.epub') return 'epub';
  return 'other';
}

module.exports = {
  fmtBytes: fmtBytes,
  fmtTime: fmtTime,
  decorateProject: decorateProject,
  decorateJob: decorateJob,
  decorateSource: decorateSource,
  decorateConfirmation: decorateConfirmation,
  decorateEvent: decorateEvent,
  specEntries: specEntries,
  inferSourceKind: inferSourceKind,
  PROJECT_STATUS: PROJECT_STATUS,
  JOB_STATUS: JOB_STATUS,
  SOURCE_STATUS: SOURCE_STATUS,
  CONFIRMATION_STATUS: CONFIRMATION_STATUS,
  SOURCE_KIND: SOURCE_KIND,
  ACTIVE_STATUSES: ACTIVE_STATUSES,
  DONE_STATUSES: DONE_STATUSES,
  FAILED_STATUSES: FAILED_STATUSES
};
