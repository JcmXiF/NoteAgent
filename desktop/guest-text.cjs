"use strict";

// What the guest browser itself says, in the two languages the app has.
//
// The interface has its own catalog; these are the few sentences the shell has to
// produce, because they leave the shell as an error message and nothing on the
// interface side can translate a refusal it did not make.

const TEXT = {
  zh: {
    htmlLang: "zh-CN",
    newTab: "新标签页",
    placeholderHeading: "尚未打开网页",
    placeholderLine1: "在此登录并浏览需要收入资料的网页，支持多标签，登录状态独立保存。",
    placeholderLine2: "从「资料」打开链接，或在上方地址栏输入 http(s) 网址。",
    notUrl: "网址无效",
    notHttp: "内置浏览器仅支持 http(s) 链接",
    noWindow: "笔记窗口尚未准备好",
    noPage: "尚未打开网页",
    noHtml: "这一页没有可保存的 HTML",
    refused: "该调用不是来自应用界面，已被拒绝",
    pickFolder: "选择要打开的文件夹",
    pickFolderButton: "打开这个文件夹",
    httpUnauthorized: "当前页仍未授权（HTTP 401）。请先登录，看到正文后再收入资料。",
    httpForbidden:
      "当前页访问被拒（HTTP 403）。请确认已登录且能看到正文，必要时刷新后再收入资料。",
  },
  en: {
    htmlLang: "en",
    newTab: "New tab",
    placeholderHeading: "No page is open yet",
    placeholderLine1:
      "Sign in to and browse pages you want to keep. Tabs are supported and the " +
      "sign-in is kept separately.",
    placeholderLine2:
      "Open a link from Sources, or type an http(s) address above.",
    notUrl: "That is not a valid address.",
    notHttp: "The guest browser opens http(s) addresses only.",
    noWindow: "The notebook window is not ready yet.",
    noPage: "No page is open.",
    noHtml: "The page has no HTML that can be saved.",
    refused: "This call came from outside the interface.",
    pickFolder: "Choose a folder to open",
    pickFolderButton: "Open this folder",
    httpUnauthorized:
      "This page still answers with HTTP 401. Sign in until you can see " +
      "the text, then keep it.",
    httpForbidden:
      "This site still answers with HTTP 403. Check that you are signed in " +
      "and can see the text, then reload before keeping it.",
  },
};

function textFor(language) {
  return TEXT[language] ?? TEXT.en;
}

module.exports = { TEXT, textFor };
