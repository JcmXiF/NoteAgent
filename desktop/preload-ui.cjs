"use strict";

const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("noteagentDesktop", {
  openGuest(url) {
    return ipcRenderer.invoke("desktop:open-guest", url);
  },
  captureGuest() {
    return ipcRenderer.invoke("desktop:capture-guest");
  },
  getGuestState() {
    return ipcRenderer.invoke("chrome:get-state");
  },
  newGuestTab(url) {
    return ipcRenderer.invoke("chrome:new-tab", url);
  },
  closeGuestTab(id) {
    return ipcRenderer.invoke("chrome:close-tab", id);
  },
  switchGuestTab(id) {
    return ipcRenderer.invoke("chrome:switch-tab", id);
  },
  navigateGuest(url) {
    return ipcRenderer.invoke("chrome:navigate", url);
  },
  guestBack() {
    return ipcRenderer.invoke("chrome:back");
  },
  guestForward() {
    return ipcRenderer.invoke("chrome:forward");
  },
  guestReload() {
    return ipcRenderer.invoke("chrome:reload");
  },
  setGuestBounds(bounds) {
    return ipcRenderer.invoke("desktop:set-guest-bounds", bounds);
  },
  pickFolder() {
    return ipcRenderer.invoke("desktop:pick-folder");
  },
  onFrame(handler) {
    const listen = () => handler();
    ipcRenderer.on("desktop:frame", listen);
    return () => ipcRenderer.removeListener("desktop:frame", listen);
  },
  onShowBrowse(handler) {
    const listen = () => handler();
    ipcRenderer.on("desktop:show-browse", listen);
    return () => ipcRenderer.removeListener("desktop:show-browse", listen);
  },
  onGuestUrl(handler) {
    const listen = (_event, url) => handler(url);
    ipcRenderer.on("desktop:guest-url", listen);
    return () => ipcRenderer.removeListener("desktop:guest-url", listen);
  },
  onGuestState(handler) {
    const listen = (_event, state) => handler(state);
    ipcRenderer.on("desktop:guest-state", listen);
    return () => ipcRenderer.removeListener("desktop:guest-state", listen);
  },
  onCaptured(handler) {
    const listen = (_event, payload) => handler(payload);
    ipcRenderer.on("desktop:captured", listen);
    return () => ipcRenderer.removeListener("desktop:captured", listen);
  },
});

// The startup screens are the shell's own pages, not the interface, so they get
// their own small bridge rather than borrowing one the interface also uses.
contextBridge.exposeInMainWorld("noteagentScreen", {
  retry() {
    return ipcRenderer.invoke("shell:retry");
  },
});
