/* 中网赛事运营管理系统 前端脚本（原生 JS，无额外依赖） */
(function () {
  'use strict';

  // 1. 提示条 6 秒后自动关闭
  document.querySelectorAll('.flash-wrap .alert').forEach(function (el) {
    setTimeout(function () {
      if (window.bootstrap && bootstrap.Alert.getOrCreateInstance(el)) {
        bootstrap.Alert.getOrCreateInstance(el).close();
      }
    }, 6000);
  });

  // 2. 带 data-confirm 的表单/按钮，提交前二次确认
  document.querySelectorAll('form[data-confirm]').forEach(function (form) {
    form.addEventListener('submit', function (e) {
      if (!window.confirm(form.getAttribute('data-confirm'))) {
        e.preventDefault();
      }
    });
  });
  document.querySelectorAll('a[data-confirm]').forEach(function (link) {
    link.addEventListener('click', function (e) {
      if (!window.confirm(link.getAttribute('data-confirm'))) {
        e.preventDefault();
      }
    });
  });

  // 3. 防止重复提交
  document.querySelectorAll('form').forEach(function (form) {
    form.addEventListener('submit', function () {
      var btn = form.querySelector('button[type="submit"]');
      if (btn) {
        setTimeout(function () { btn.disabled = true; }, 10);
        setTimeout(function () { btn.disabled = false; }, 8000);
      }
    });
  });

  // 4. 日期输入框默认值：data-default-today="1"
  document.querySelectorAll('input[data-default-today="1"]').forEach(function (input) {
    if (!input.value) {
      var today = new Date();
      var mm = String(today.getMonth() + 1).padStart(2, '0');
      var dd = String(today.getDate()).padStart(2, '0');
      input.value = today.getFullYear() + '-' + mm + '-' + dd;
    }
  });

  // 5. 表格内数字输入实时校验（比分格式）
  document.querySelectorAll('input[data-pattern-hint]').forEach(function (input) {
    input.addEventListener('input', function () {
      var re = new RegExp(input.getAttribute('data-pattern'));
      var ok = re.test(input.value.trim());
      input.classList.toggle('is-invalid', input.value.trim() !== '' && !ok);
      input.classList.toggle('is-valid', input.value.trim() !== '' && ok);
    });
  });
})();
