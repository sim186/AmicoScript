import { mount } from "svelte";
import "./app.css";
import App from "./App.svelte";

const target = document.getElementById("app");
if (!target) {
  throw new Error("#app mount point is missing from index.html");
}

export default mount(App, { target });
