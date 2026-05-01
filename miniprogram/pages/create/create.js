// pages/create/create.js — 选模板 + 表单
const api = require('../../utils/api.js');
const auth = require('../../utils/auth.js');

const PAGE_LIMIT = { min: 1, max: 60 };

// 本地兜底模板列表（与后端 service/api/routes/projects.py:_TEMPLATES 保持一致）
const FALLBACK_TEMPLATES = [
  { canvas_format: 'ppt169',     label: 'PPT 16:9 演示',     aspect_ratio: '16:9', use_case: '商务演示、会议汇报' },
  { canvas_format: 'ppt43',      label: 'PPT 4:3 传统',      aspect_ratio: '4:3',  use_case: '传统投影、学术演讲' },
  { canvas_format: 'xiaohongshu',label: '小红书图文',         aspect_ratio: '3:4',  use_case: '图文分享、知识帖' },
  { canvas_format: 'square',     label: '朋友圈/IG 方图',     aspect_ratio: '1:1',  use_case: '正方海报、品牌展示' },
  { canvas_format: 'story',      label: 'Story / 抖音竖屏',   aspect_ratio: '9:16', use_case: '竖屏故事、短视频封面' },
  { canvas_format: 'banner169',  label: 'Landscape Banner',  aspect_ratio: '16:9', use_case: '网页 banner、数字屏' },
  { canvas_format: 'a4',         label: 'A4 打印',           aspect_ratio: '1:√2', use_case: '打印海报、单页传单' }
];

Page({
  data: {
    templates: [],
    loadingTpl: true,
    fallbackUsed: false,
    showTplPicker: false,
    selectedFmt: '',
    selectedTpl: null,
    projectName: '',
    pageMin: 8,
    pageMax: 12,
    sourceHint: '',
    submitting: false
  },

  async onLoad() {
    if (!auth.isLoggedIn()) {
      wx.showToast({ title: '请先登录', icon: 'none' });
      setTimeout(() => wx.switchTab({ url: '/pages/index/index' }), 800);
      return;
    }
    try {
      const tpl = await api.templates();
      const list = (tpl && tpl.length) ? tpl : FALLBACK_TEMPLATES;
      this.setData({
        templates: list,
        selectedFmt: list[0].canvas_format,
        selectedTpl: list[0],
        loadingTpl: false,
        fallbackUsed: !(tpl && tpl.length)
      });
    } catch (_e) {
      // 网络/后端不可用 —— 使用本地兜底模板
      this.setData({
        templates: FALLBACK_TEMPLATES,
        selectedFmt: FALLBACK_TEMPLATES[0].canvas_format,
        selectedTpl: FALLBACK_TEMPLATES[0],
        loadingTpl: false,
        fallbackUsed: true
      });
    }
  },

  onOpenTplPicker() {
    if (this.data.loadingTpl) return;
    this.setData({ showTplPicker: true });
  },

  onCloseTplPicker() {
    this.setData({ showTplPicker: false });
  },

  onTplSheetTap() {},

  onSelectTpl(e) {
    if (wx.vibrateShort) wx.vibrateShort({ type: 'light' });
    const fmt = e.currentTarget.dataset.fmt;
    const selectedTpl = (this.data.templates || []).find(item => item.canvas_format === fmt) || null;
    this.setData({
      selectedFmt: fmt,
      selectedTpl: selectedTpl,
      showTplPicker: false
    });
  },
  onNameInput(e) { this.setData({ projectName: e.detail.value }); },
  onMinInput(e) {
    const v = Math.max(PAGE_LIMIT.min, Math.min(PAGE_LIMIT.max, Number(e.detail.value) || 1));
    this.setData({ pageMin: v });
  },
  onMaxInput(e) {
    const v = Math.max(PAGE_LIMIT.min, Math.min(PAGE_LIMIT.max, Number(e.detail.value) || 1));
    this.setData({ pageMax: v });
  },
  onHintInput(e) { this.setData({ sourceHint: e.detail.value }); },

  async onSubmit() {
    const name = this.data.projectName.trim();
    if (!name) return wx.showToast({ title: '请输入项目名', icon: 'none' });
    if (name.length > 60) return wx.showToast({ title: '项目名过长', icon: 'none' });
    if (!this.data.selectedFmt) return wx.showToast({ title: '请选择模板', icon: 'none' });
    if (this.data.pageMax < this.data.pageMin) {
      return wx.showToast({ title: '页数上限需 ≥ 下限', icon: 'none' });
    }

    this.setData({ submitting: true });
    try {
      const project = await api.createProject({
        project_name: name,
        canvas_format: this.data.selectedFmt,
        requested_page_min: this.data.pageMin,
        requested_page_max: this.data.pageMax,
        source_type_hint: this.data.sourceHint || null
      });
      wx.redirectTo({ url: '/pages/detail/detail?pid=' + project.project_id });
    } catch (e) {
      wx.showToast({ title: e.message || '创建失败', icon: 'none' });
    } finally {
      this.setData({ submitting: false });
    }
  }
});
