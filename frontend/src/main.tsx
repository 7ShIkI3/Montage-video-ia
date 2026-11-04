import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { Editor } from "./components/Editor";
import { Home } from "./components/Home";
import "./styles.css";

function route(): string | null {
  const m = location.hash.match(/^#\/p\/([\w]+)/);
  return m ? m[1] : null;
}

function App() {
  const [pid, setPid] = useState<string | null>(route());
  useEffect(() => {
    const on = () => setPid(route());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return pid ? (
    <Editor key={pid} id={pid} onBack={() => (location.hash = "")} />
  ) : (
    <Home onOpen={(id) => (location.hash = `#/p/${id}`)} />
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
