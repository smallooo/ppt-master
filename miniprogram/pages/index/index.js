// pages/index/index.js — 项目列表 + 登录入口
const auth = require('../../utils/auth.js');
const api = require('../../utils/api.js');
const fmt = require('../../utils/format.js');

const TABS = [
  { key: 'all',     label: '全部' },
  { key: 'active',  label: '进行中' },
  { key: 'done',    label: '已完成' },
  { key: 'failed',  label: '异常' }
];

Page({
  data: {
    loggedIn: false,
    loading: false,
    projects: [],
    filtered: [],
    tabKey: 'all',
    tabs: TABS,
    user: null,
    online: true,
    counts: { all: 0, active: 0, done: 0, failed: 0 }
  },

  onShow() {
    const app = getApp();
    this.setData({ online: app.globalData.online });
    this.refresh();
  },

  async refresh() {
    const app = getApp();
    this.setData({ online: app.globalData.online });
    if (!auth.isLoggedIn()) {
      this.setData({ loggedIn: false, projects: [], filtered: [], user: null });
      return;
    }
    this.setData({ loggedIn: true, loading: true, user: app.globalData.user });
    try {
      const list = await api.listProjects();
      const decorated = (list || []).map(fmt.decorateProject);
      const counts = {
        all: decorated.length,
        active: decorated.filter(p => fmt.ACTIVE_STATUSES.indexOf(p.status) >= 0).length,
        done: decorated.filter(p => fmt.DONE_STATUSES.indexOf(p.status) >= 0).length,
        failed: decorated.filter(p => fmt.FAILED_STATUSES.indexOf(p.status) >= 0).length
      };
      this.setData({ projects: decorated, counts: counts });
      this._applyFilter();
    } catch (e) {
      wx.showToast({ title: e.message || '加载失败', icon: 'none' });
    } finally {
      this.setData({ loading: false });
    }
  },

  _applyFilter() {
    const k = this.data.tabKey;
    const list = this.data.projects.filter(p => {
      if (k === 'active') return fmt.ACTIVE_STATUSES.indexOf(p.status) >= 0;
      if (k === 'done')   return fmt.DONE_STATUSES.indexOf(p.status) >= 0;
      if (k === 'failed') return fmt.FAILED_STATUSES.indexOf(p.status) >= 0;
      return true;
    });
    this.setData({ filtered: list });
  },

  onTabChange(e) {
    this.setData({ tabKey: e.currentTarget.dataset.key });
    this._applyFilter();
  },

  async onLoginTap() {
    try {
      wx.showLoading({ title: '微信授权中', mask: true });
      await auth.loginWithWeChat();
      wx.hideLoading();
      this.refresh();
    } catch (e) {
      wx.hideLoading();
      wx.showModal({
        title: '登录失败',
        content: (e && e.message) || '未知错误',
        confirmText: '我知道了',
        showCancel: false
      });
    }
  },

  onCreateTap() {
    if (wx.vibrateShort) wx.vibrateShort({ type: 'light' });
    wx.navigateTo({ url: '/pages/create/create' });
  },

  onProjectTap(e) {
    wx.navigateTo({ url: '/pages/detail/detail?pid=' + e.currentTarget.dataset.pid });
  },

  onPullDownRefresh() {
    this.refresh().finally(() => wx.stopPullDownRefresh());
  }
});
