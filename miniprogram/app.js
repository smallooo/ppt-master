// PPT Master 微信小程序入口
const { restoreToken } = require('./utils/auth.js');

App({
  // 在小程序后台 -> 开发设置 -> 服务器域名里加入这个域名（HTTPS + WSS）。
  // 本地联调可在微信开发者工具里关闭"不校验合法域名"。
  globalData: {
    baseUrl: 'http://127.0.0.1:8000',   // TODO: 改成你的后端地址，必须是 HTTPS 协议
    user: null,
    token: null,
    expiresAt: null,
    adminToken: null,
    online: true,
    networkType: 'unknown'
  },

  onLaunch() {
    restoreToken();

    // 网络监听
    wx.getNetworkType({
      success: res => {
        this.globalData.networkType = res.networkType;
        this.globalData.online = res.networkType !== 'none';
      }
    });
    wx.onNetworkStatusChange(res => {
      const wasOnline = this.globalData.online;
      this.globalData.online = res.isConnected;
      this.globalData.networkType = res.networkType;
      if (wasOnline && !res.isConnected) {
        wx.showToast({ title: '网络已断开', icon: 'none' });
      } else if (!wasOnline && res.isConnected) {
        wx.showToast({ title: '网络已恢复', icon: 'success' });
      }
    });

    // baseUrl 占位提醒
    if (/your-domain\.example\.com/.test(this.globalData.baseUrl)) {
      console.warn('[PPT Master] baseUrl 仍为占位地址，请在 app.js 中改成真实后端域名');
    }
  },

  onError(err) {
    console.error('[App.onError]', err);
  },
  onPageNotFound(res) {
    console.warn('[App.onPageNotFound]', res);
    wx.switchTab({ url: '/pages/index/index' });
  }
});
