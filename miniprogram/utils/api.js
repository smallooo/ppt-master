// 业务接口集中导出 — 便于页面侧调用，签名贴近后端路由。
const { request, uploadFile } = require('./request.js');

const api = {
  // -------- 用户 --------
  me: function () { return request({ path: '/api/v1/mini/me' }); },
  quota: function () { return request({ path: '/api/v1/mini/quota' }); },

  // -------- 模板 --------
  templates: function () { return request({ path: '/api/v1/mini/templates', auth: false }); },

  // -------- 项目 --------
  listProjects: function () { return request({ path: '/api/v1/mini/projects' }); },
  createProject: function (body) {
    return request({ method: 'POST', path: '/api/v1/mini/projects', data: body });
  },
  getProject: function (pid) { return request({ path: '/api/v1/mini/projects/' + pid }); },
  patchProject: function (pid, patch) {
    return request({ method: 'PATCH', path: '/api/v1/mini/projects/' + pid, data: patch });
  },
  deleteProject: function (pid) {
    return request({ method: 'DELETE', path: '/api/v1/mini/projects/' + pid });
  },

  // -------- 来源文件 --------
  listSources: function (pid) {
    return request({ path: '/api/v1/mini/projects/' + pid + '/sources' });
  },
  uploadSource: function (pid, filePath, kind, role) {
    return uploadFile({
      path: '/api/v1/mini/projects/' + pid + '/sources',
      filePath: filePath,
      formData: { source_kind: kind || 'markdown', role: role || 'primary_source' }
    });
  },
  uploadSourceUrl: function (pid, url, role) {
    return request({
      method: 'POST',
      path: '/api/v1/mini/projects/' + pid + '/sources/url',
      data: { url: url, role: role || 'primary_source' }
    });
  },
  deleteSource: function (pid, sid) {
    return request({
      method: 'DELETE',
      path: '/api/v1/mini/projects/' + pid + '/sources/' + sid
    });
  },
  finalizeSources: function (pid) {
    return request({ method: 'POST', path: '/api/v1/mini/projects/' + pid + '/sources/finalize' });
  },

  // -------- 确认 --------
  getConfirmation: function (pid) {
    return request({ path: '/api/v1/mini/projects/' + pid + '/confirmation' });
  },

  // -------- 任务 --------
  generate: function (pid) {
    return request({ method: 'POST', path: '/api/v1/mini/projects/' + pid + '/jobs/generate' });
  },
  latestJob: function (pid) {
    return request({ path: '/api/v1/mini/projects/' + pid + '/jobs/latest' });
  },
  listJobs: function (pid) {
    return request({ path: '/api/v1/mini/projects/' + pid + '/jobs' });
  },
  jobEvents: function (pid, jid) {
    return request({ path: '/api/v1/mini/projects/' + pid + '/jobs/' + jid + '/events' });
  },
  cancelJob: function (pid, jid) {
    return request({
      method: 'POST',
      path: '/api/v1/mini/projects/' + pid + '/jobs/' + jid + '/cancel'
    });
  },

  // -------- 产物 --------
  listArtifacts: function (pid) {
    return request({ path: '/api/v1/mini/projects/' + pid + '/artifacts' });
  },
  // 下载使用 utils/request.js 里的 downloadFile
  downloadPath_pptx: function (pid) {
    return '/api/v1/mini/projects/' + pid + '/download/pptx';
  },
  downloadPath_artifact: function (pid, aid) {
    return '/api/v1/mini/projects/' + pid + '/download/' + aid;
  },
  previewPath: function (pid) {
    return '/api/v1/mini/projects/' + pid + '/preview';
  },

  // -------- 管理端（需要 X-Admin-Token） --------
  adminApprove: function (pid, body) {
    return request({
      method: 'POST',
      path: '/api/v1/admin/projects/' + pid + '/confirmation/approve',
      data: body, admin: true
    });
  },
  adminReject: function (pid, body) {
    return request({
      method: 'POST',
      path: '/api/v1/admin/projects/' + pid + '/confirmation/reject',
      data: body, admin: true
    });
  }
};

module.exports = api;
