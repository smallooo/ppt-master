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
      return { key: 'finalize', label: '完成上传，进入解析', disabled: this.data.sources.length === 0 };
    }
    if (s === 'normalizing') return { key: 'wait', label: '解析中…', disabled: true };
    if (s === 'awaiting_confirmation') {
      return { key: 'generate', label: '🚀 开始生成 PPT' };
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
      this.setData({
        confirmation: c,
        specEntries: fmt.specEntries(c.suggested_spec || {}),
        confirmationSpecText: JSON.stringify(c.suggested_spec || {}, null, 2)
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
    if (!this.data.sources.length) {
      return wx.showToast({ title: '请先上传至少一个素材', icon: 'none' });
    }
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
