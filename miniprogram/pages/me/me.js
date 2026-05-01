// pages/me/me.js — 个人中心
const auth = require('../../utils/auth.js');
const api = require('../../utils/api.js');
const fmt = require('../../utils/format.js');

Page({
  data: {
    user: null,
    quota: null,
    quotaText: { storage: '-', project: '-' },
    quotaWarn: '',         // 配额提醒
    online: true,
    loading: false
  },

  onShow() {
    const app = getApp();
    this.setData({
      online: app.globalData.online
    });
    if (!auth.isLoggedIn()) {
      this.setData({ user: null, quota: null });
      return;
    }
    this._load();
  },

  async _load() {
    this.setData({ loading: true });
    try {
      const [u, q] = await Promise.all([
        api.me().catch(() => null),
        api.quota().catch(() => null)
      ]);
      const quotaText = {
        storage: q ? (fmt.fmtBytes(q.storage_bytes) + (q.storage_quota_bytes ? ' / ' + fmt.fmtBytes(q.storage_quota_bytes) : '')) : '-',
        project: q ? (q.project_count + (q.project_quota ? ' / ' + q.project_quota : '')) : '-'
      };
      let warn = '';
      if (q && q.project_quota && q.project_count >= q.project_quota) {
        warn = '⚠️ 项目配额已用完';
      } else if (q && q.storage_quota_bytes && q.storage_bytes / q.storage_quota_bytes > 0.9) {
        warn = '⚠️ 存储空间使用已超 90%';
      }
      this.setData({ user: u, quota: q, quotaText: quotaText, quotaWarn: warn });
    } catch (_e) {
    } finally {
      this.setData({ loading: false });
    }
  },

  onLoginTap() {
    auth.loginWithWeChat()
      .then(() => this.onShow())
      .catch(e => wx.showModal({
        title: '登录失败',
        content: (e && e.message) || '未知错误',
        confirmText: '我知道了',
        showCancel: false
      }));
  },

  async onLogoutTap() {
    const ok = await new Promise(r => wx.showModal({
      title: '退出登录', content: '确定退出当前账号？',
      success: res => r(res.confirm), fail: () => r(false)
    }));
    if (!ok) return;
    await auth.logout();
    this.setData({ user: null, quota: null, quotaWarn: '' });
    wx.showToast({ title: '已退出', icon: 'none' });
  },

  onCopyOpenid() {
    if (!this.data.user || !this.data.user.openid) return;
    wx.setClipboardData({
      data: this.data.user.openid,
      success() { wx.showToast({ title: 'openid 已复制', icon: 'success' }); }
    });
  },

  onPullDownRefresh() {
    this._load().finally(() => wx.stopPullDownRefresh());
  }
});
