// pages/detail/detail.js — 项目工作台
const api = require('../../utils/api.js');
const { downloadFile } = require('../../utils/request.js');
const fmt = require('../../utils/format.js');

const POLL_MS = 2500;

Page({
  data: {
    pid: '',
    project: null,
    sources: [],
    confirmation: null,
    confirmationSpecText: '',
    outlineDraft: '',
    pageBriefs: [],
    originalPageBriefs: [],
    specEntries: [],
    job: null,
    events: [],
    eventsView: [],
    jobs: [],
    artifacts: [],
    urlInput: '',
    polling: false,
    showAllEvents: false,
    showJobs: false,
    showSpec: false,
    online: true,
    // 派生：主操作按钮
    primaryAction: null,   // { key, label, danger?, disabled? }
    adminMode: false
  },

  onLoad(opts) {
    const app = getApp();
    this.setData({
      pid: opts.pid,
      adminMode: !!app.globalData.adminToken,
      online: app.globalData.online
    });
  },

  onShow() {
    const app = getApp();
    this.setData({
      online: app.globalData.online,
      adminMode: !!app.globalData.adminToken
    });
    this.refreshAll();
  },
  onUnload() { this._stopPolling(); },
  onHide() { this._stopPolling(); },

  // ===== 派生主操作 =====
  _computePrimaryAction(project, confirmation, job) {
    if (!project) return null;
    const s = project.status;
    if (s === 'created' || s === 'uploading') {
      if (this.data.sources.length === 0) {
        return { key: 'finalize', label: '📝 生成默认提纲' };
      }
      return { key: 'finalize', label: '完成上传，进入解析' };
    }
    if (s === 'normalizing') return { key: 'wait', label: '解析中…', disabled: true };
    if (s === 'awaiting_confirmation') {
      const phase = confirmation && confirmation.suggested_spec && confirmation.suggested_spec.confirmation_phase;
      if (phase === 'page_briefs') {
        return { key: 'approvePageBriefs', label: '✅ 确认每页内容' };
      }
      return { key: 'approveOutline', label: '✅ 确认提纲' };
    }
    if (s === 'ready_to_generate') {
      return { key: 'generate', label: '🚀 开始生成 PPT' };
    }
    if (s === 'generating' || s === 'post_processing' || s === 'exporting') {
      return job && job.job_id
        ? { key: 'cancel', label: '取消生成', danger: true }
        : { key: 'wait', label: '生成中…', disabled: true };
    }
    if (s === 'completed') {
      return { key: 'download', label: '📥 下载 PPTX' };
    }
    if (s === 'failed' || s === 'cancelled') {
      return { key: 'generate', label: '重新生成' };
    }
    return null;
  },

  async refreshAll() {
    if (!this.data.pid) return;
    try {
      const project = fmt.decorateProject(await api.getProject(this.data.pid));
      this.setData({ project: project });
      wx.setNavigationBarTitle({ title: project.project_name || '项目详情' });

      const tasks = [
        api.listSources(this.data.pid)
          .then(s => this.setData({ sources: (s || []).map(fmt.decorateSource) }))
          .catch(() => {}),
        api.listArtifacts(this.data.pid)
          .then(a => this.setData({ artifacts: a || [] }))
          .catch(() => {})
      ];
      if (project.status !== 'created' && project.status !== 'uploading') {
        tasks.push(this._refreshConfirmation());
      } else {
        this.setData({ confirmation: null, specEntries: [] });
      }
      const jobStates = ['ready_to_generate','generating','post_processing','exporting','completed','failed','cancelled'];
      if (jobStates.indexOf(project.status) >= 0) {
        tasks.push(this._refreshJob());
      }
      await Promise.all(tasks);

      // 计算主操作
      this.setData({ primaryAction: this._computePrimaryAction(project, this.data.confirmation, this.data.job) });

      const polling = ['generating','post_processing','exporting','normalizing'].indexOf(project.status) >= 0;
      if (polling) this._startPolling();
      else this._stopPolling();
    } catch (e) {
      wx.showToast({ title: e.message || '加载失败', icon: 'none' });
    }
  },

  async _refreshConfirmation() {
    try {
      const c = fmt.decorateConfirmation(await api.getConfirmation(this.data.pid));
      const pageBriefs = (c.suggested_spec && c.suggested_spec.page_briefs) || [];
      this.setData({
        confirmation: c,
        specEntries: fmt.specEntries(c.suggested_spec || {}),
        confirmationSpecText: JSON.stringify(c.suggested_spec || {}, null, 2),
        outlineDraft: (c.suggested_spec && c.suggested_spec.outline_markdown) || '',
        pageBriefs: pageBriefs,
        originalPageBriefs: JSON.parse(JSON.stringify(pageBriefs))
      });
    } catch (_e) {
      // 项目早期阶段可能 404
    }
  },

  async _refreshJob() {
    try {
      const j = fmt.decorateJob(await api.latestJob(this.data.pid));
      this.setData({ job: j });
      if (j && j.job_id) {
        const ev = await api.jobEvents(this.data.pid, j.job_id).catch(() => []);
        const sorted = (ev || []).slice().reverse().map(fmt.decorateEvent);
        this.setData({
          events: sorted,
          eventsView: this.data.showAllEvents ? sorted : sorted.slice(0, 4)
        });
      }
    } catch (_e) {}
  },

  _startPolling() {
    if (this.data.polling) return;
    this.setData({ polling: true });
    const tick = async () => {
      if (!this._timer) return;
      try { await this._refreshJob(); } catch (_e) {}
      if (!this._timer) return;
      const j = this.data.job;
      if (j && j._isFinal) {
        this._stopPolling();
        await this.refreshAll();
      }
    };
    this._timer = setInterval(tick, POLL_MS);
  },

  _stopPolling() {
    if (this._timer) { clearInterval(this._timer); this._timer = null; }
    this.setData({ polling: false });
  },

  // ===== 主操作分发 =====
  onPrimaryTap() {
    const a = this.data.primaryAction;
    if (!a || a.disabled) return;
    if (wx.vibrateShort) wx.vibrateShort({ type: 'light' });
    if (a.key === 'finalize') return this.onFinalize();
    if (a.key === 'approveOutline') return this.onApproveOutline();
    if (a.key === 'approvePageBriefs') return this.onApprovePageBriefs();
    if (a.key === 'generate') return this.onGenerate();
    if (a.key === 'cancel')   return this.onCancelJob();
    if (a.key === 'download') return this.onDownloadPptx();
  },

  // ===== 项目操作 =====
  async onRenameTap() {
    const project = this.data.project;
    if (!project) return;
    const res = await new Promise(r => wx.showModal({
      title: '重命名项目',
      editable: true,
      content: project.project_name,
      placeholderText: '输入新名称',
      success: r, fail: () => r({ confirm: false })
    }));
    if (!res.confirm) return;
    const name = (res.content || '').trim();
    if (!name) return;
    try {
      await api.patchProject(this.data.pid, { project_name: name });
      this.refreshAll();
    } catch (e) {
      wx.showToast({ title: e.message || '修改失败', icon: 'none' });
    }
  },

  async onDeleteProject() {
    const ok = await new Promise(r => wx.showModal({
      title: '删除项目', content: '将同时删除全部素材与产物，确定？',
      confirmColor: '#dc2626',
      success: res => r(res.confirm), fail: () => r(false)
    }));
    if (!ok) return;
    try {
      await api.deleteProject(this.data.pid);
      wx.navigateBack();
    } catch (e) {
      wx.showToast({ title: e.message || '删除失败', icon: 'none' });
    }
  },

  // ===== 素材 =====
  async onPickFile() {
    try {
      const res = await wx.chooseMessageFile({
        count: 1,
        type: 'file',
        extension: ['md','markdown','txt','pdf','docx','doc','pptx','ppt','xlsx','xls','xlsm','csv','epub','html','htm']
      });
      const file = res.tempFiles[0];
      const kind = fmt.inferSourceKind(file.name);
      wx.showLoading({ title: '上传中', mask: true });
      await api.uploadSource(this.data.pid, file.path, kind, 'primary_source');
      wx.hideLoading();
      this.refreshAll();
    } catch (e) {
      wx.hideLoading();
      if (e && e.errMsg && e.errMsg.indexOf('cancel') >= 0) return;
      wx.showToast({ title: (e && e.message) || '上传失败', icon: 'none' });
    }
  },

  onUrlInput(e) { this.setData({ urlInput: e.detail.value }); },

  async onAddUrl() {
    const url = this.data.urlInput.trim();
    if (!url) return;
    if (!/^https?:\/\//i.test(url)) {
      return wx.showToast({ title: '请输入以 http(s):// 开头的链接', icon: 'none' });
    }
    try {
      wx.showLoading({ title: '抓取中', mask: true });
      await api.uploadSourceUrl(this.data.pid, url);
      wx.hideLoading();
      this.setData({ urlInput: '' });
      this.refreshAll();
    } catch (e) {
      wx.hideLoading();
      wx.showToast({ title: e.message || '抓取失败', icon: 'none' });
    }
  },

  async onDeleteSource(e) {
    const sid = e.currentTarget.dataset.sid;
    const ok = await new Promise(r => wx.showModal({
      title: '删除素材', content: '该素材将被移除',
      success: res => r(res.confirm), fail: () => r(false)
    }));
    if (!ok) return;
    try {
      await api.deleteSource(this.data.pid, sid);
      this.refreshAll();
    } catch (err) {
      wx.showToast({ title: err.message || '删除失败', icon: 'none' });
    }
  },

  async onFinalize() {
    try {
      wx.showLoading({ title: '解析中', mask: true });
      await api.finalizeSources(this.data.pid);
      wx.hideLoading();
      this.refreshAll();
    } catch (e) {
      wx.hideLoading();
      wx.showToast({ title: e.message || '解析失败', icon: 'none' });
    }
  },

  onOutlineInput(e) {
    this.setData({ outlineDraft: e.detail.value });
  },

  _normalizePageBriefs(pageBriefs) {
    return (pageBriefs || []).map((brief, index) => Object.assign({}, brief, {
      page_no: index + 1,
      title: brief && brief.title ? brief.title : `第 ${index + 1} 页`,
      summary: brief && brief.summary ? brief.summary : '',
      bullets: Array.isArray(brief && brief.bullets) ? brief.bullets : [],
      bulletsText: Array.isArray(brief && brief.bullets)
        ? brief.bullets.map(item => String(item || '')).join('\n')
        : ''
    }));
  },

  _updatePageBrief(index, patch) {
    const pageBriefs = this._normalizePageBriefs((this.data.pageBriefs || []).slice());
    const current = Object.assign({}, pageBriefs[index] || {});
    pageBriefs[index] = Object.assign(current, patch);
    this.setData({ pageBriefs: pageBriefs });
  },

  onAddPageBrief() {
    const pageBriefs = this._normalizePageBriefs((this.data.pageBriefs || []).slice());
    pageBriefs.push({
      page_no: pageBriefs.length + 1,
      title: `第 ${pageBriefs.length + 1} 页`,
      summary: '',
      bullets: []
    });
    this.setData({ pageBriefs: pageBriefs });
  },

  onDeletePageBrief(e) {
    const index = Number(e.currentTarget.dataset.index);
    const pageBriefs = this._normalizePageBriefs((this.data.pageBriefs || []).slice());
    if (pageBriefs.length <= 1) {
      return wx.showToast({ title: '至少保留一页', icon: 'none' });
    }
    pageBriefs.splice(index, 1);
    this.setData({ pageBriefs: this._normalizePageBriefs(pageBriefs) });
  },

  onMovePageBriefUp(e) {
    const index = Number(e.currentTarget.dataset.index);
    if (index <= 0) return;
    const pageBriefs = this._normalizePageBriefs((this.data.pageBriefs || []).slice());
    const temp = pageBriefs[index - 1];
    pageBriefs[index - 1] = pageBriefs[index];
    pageBriefs[index] = temp;
    this.setData({ pageBriefs: this._normalizePageBriefs(pageBriefs) });
  },

  onMovePageBriefDown(e) {
    const index = Number(e.currentTarget.dataset.index);
    const pageBriefs = this._normalizePageBriefs((this.data.pageBriefs || []).slice());
    if (index >= pageBriefs.length - 1) return;
    const temp = pageBriefs[index + 1];
    pageBriefs[index + 1] = pageBriefs[index];
    pageBriefs[index] = temp;
    this.setData({ pageBriefs: this._normalizePageBriefs(pageBriefs) });
  },

  async onRestorePageBriefs() {
    const original = this.data.originalPageBriefs || [];
    if (!original.length) {
      return wx.showToast({ title: '没有可恢复的版本', icon: 'none' });
    }
    const ok = await new Promise(r => wx.showModal({
      title: '恢复系统版本',
      content: '将用系统生成的每页简要内容覆盖当前编辑结果，确定继续？',
      success: res => r(res.confirm),
      fail: () => r(false)
    }));
    if (!ok) return;
    this.setData({
      pageBriefs: this._normalizePageBriefs(JSON.parse(JSON.stringify(original)))
    });
  },

  onPageBriefTitleInput(e) {
    const index = Number(e.currentTarget.dataset.index);
    this._updatePageBrief(index, { title: e.detail.value });
  },

  onPageBriefSummaryInput(e) {
    const index = Number(e.currentTarget.dataset.index);
    this._updatePageBrief(index, { summary: e.detail.value });
  },

  onPageBriefBulletsInput(e) {
    const index = Number(e.currentTarget.dataset.index);
    const bullets = (e.detail.value || '')
      .split('\n')
      .map(item => item.trim())
      .filter(Boolean);
    this._updatePageBrief(index, { bullets: bullets });
  },

  async onApproveOutline() {
    const confirmation = this.data.confirmation;
    if (!confirmation) return;
    const outlineDraft = (this.data.outlineDraft || '').trim();
    if (!outlineDraft) {
      return wx.showToast({ title: '请先填写提纲内容', icon: 'none' });
    }
    try {
      wx.showLoading({ title: '提交确认中', mask: true });
      await api.approveConfirmation(this.data.pid, {
        approved_by: 'user',
        updated_spec: Object.assign({}, confirmation.suggested_spec || {}, {
          outline_markdown: outlineDraft
        })
      });
      wx.hideLoading();
      wx.showToast({ title: '提纲已确认', icon: 'success' });
      this.refreshAll();
    } catch (e) {
      wx.hideLoading();
      wx.showToast({ title: e.message || '确认失败', icon: 'none' });
    }
  },

  async onApprovePageBriefs() {
    const confirmation = this.data.confirmation;
    if (!confirmation) return;
    const pageBriefs = this._normalizePageBriefs(this.data.pageBriefs || []).map((brief, index) => ({
      page_no: brief.page_no || index + 1,
      title: (brief.title || '').trim() || `第 ${index + 1} 页`,
      summary: (brief.summary || '').trim(),
      bullets: Array.isArray(brief.bullets) ? brief.bullets.map(item => String(item).trim()).filter(Boolean) : []
    }));
    if (!pageBriefs.length) {
      return wx.showToast({ title: '请先确认每页内容', icon: 'none' });
    }
    try {
      wx.showLoading({ title: '确认中', mask: true });
      await api.approveConfirmation(this.data.pid, {
        approved_by: 'user',
        updated_spec: Object.assign({}, confirmation.suggested_spec || {}, {
          page_briefs: pageBriefs
        })
      });
      wx.hideLoading();
      wx.showToast({ title: '已可开始生成', icon: 'success' });
      this.refreshAll();
    } catch (e) {
      wx.hideLoading();
      wx.showToast({ title: e.message || '确认失败', icon: 'none' });
    }
  },

  // ===== 生成 =====
  async onGenerate() {
    try {
      wx.showLoading({ title: '正在派发', mask: true });
      await api.generate(this.data.pid);
      wx.hideLoading();
      wx.showToast({ title: '已开始生成', icon: 'success' });
      this.refreshAll();
    } catch (e) {
      wx.hideLoading();
      wx.showToast({ title: e.message || '生成失败', icon: 'none' });
    }
  },

  async onCancelJob() {
    if (!this.data.job) return;
    const ok = await new Promise(r => wx.showModal({
      title: '取消生成', content: '当前任务将被中止，已完成的页面会保留。',
      confirmColor: '#dc2626',
      success: res => r(res.confirm), fail: () => r(false)
    }));
    if (!ok) return;
    try {
      await api.cancelJob(this.data.pid, this.data.job.job_id);
      this.refreshAll();
    } catch (e) {
      wx.showToast({ title: e.message || '取消失败', icon: 'none' });
    }
  },

  async onToggleJobs() {
    const next = !this.data.showJobs;
    this.setData({ showJobs: next });
    if (next && this.data.jobs.length === 0) {
      try {
        const list = await api.listJobs(this.data.pid);
        this.setData({ jobs: (list || []).map(fmt.decorateJob) });
      } catch (_e) {}
    }
  },

  onToggleEvents() {
    const next = !this.data.showAllEvents;
    this.setData({
      showAllEvents: next,
      eventsView: next ? this.data.events : this.data.events.slice(0, 4)
    });
  },
  onToggleSpec() { this.setData({ showSpec: !this.data.showSpec }); },

  // ===== 产物 =====
  async onDownloadPptx() {
    try {
      wx.showLoading({ title: '下载中', mask: true });
      const res = await downloadFile(api.downloadPath_pptx(this.data.pid));
      wx.hideLoading();
      wx.openDocument({
        filePath: res.tempFilePath,
        fileType: 'pptx',
        showMenu: true,
        fail() { wx.showToast({ title: '已保存到临时文件', icon: 'none' }); }
      });
    } catch (e) {
      wx.hideLoading();
      wx.showToast({ title: e.message || '下载失败', icon: 'none' });
    }
  },

  async onDownloadArtifact(e) {
    const aid = e.currentTarget.dataset.aid;
    try {
      wx.showLoading({ title: '下载中', mask: true });
      const res = await downloadFile(api.downloadPath_artifact(this.data.pid, aid));
      wx.hideLoading();
      wx.shareFileMessage({
        filePath: res.tempFilePath,
        fail() { wx.showToast({ title: '已下载', icon: 'success' }); }
      });
    } catch (err) {
      wx.hideLoading();
      wx.showToast({ title: err.message || '下载失败', icon: 'none' });
    }
  },

  onCopyPreviewLink() {
    const url = (getApp().globalData.baseUrl || '').replace(/\/$/, '') + api.previewPath(this.data.pid);
    wx.setClipboardData({
      data: url,
      success() { wx.showToast({ title: '预览链接已复制', icon: 'success' }); }
    });
  },

  onShareAppMessage() {
    const p = this.data.project;
    return {
      title: p ? p.project_name : 'PPT Master 项目',
      path: '/pages/detail/detail?pid=' + this.data.pid
    };
  },

  // ===== 管理端 =====
  onPullDownRefresh() {
    this.refreshAll().finally(() => wx.stopPullDownRefresh());
  }
});
