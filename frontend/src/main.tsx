import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
// Polices embarquées : l'application marche hors ligne (plus de Google Fonts).
import "@fontsource-variable/inter/index.css";
import "@fontsource/space-grotesk/500.css";
import "@fontsource/space-grotesk/600.css";
import "@fontsource/space-grotesk/700.css";
import "@fontsource/jetbrains-mono/500.css";
// Ordre de la cascade : styles.css, puis les briques partagées, puis les feuilles des composants (importées par eux).
// Ces deux imports doivent rester AVANT ceux des composants.
import "./styles.css";
import "./styles/ui.css";
import { Editor } from "./components/Editor";
import { Home } from "./components/Home";
import { Settings } from "./components/Settings";
import { Toasts } from "./components/Toasts";
import { DialogHost } from "./components/Dialog";
import { useStore } from "./store";

type Route = { page: "projet"; id: string } | { page: "reglages" } | { page: "accueil" };

function route(): Route {
  if (location.hash.startsWith("#/reglages")) return { page: "reglages" };
  const m = location.hash.match(/^#\/p\/([\w]+)/);
  return m ? { page: "projet", id: m[1] } : { page: "accueil" };
}

// Les erreurs que personne n'a rattrapées deviennent un message visible au lieu de disparaître dans la console.
window.addEventListener("unhandledrejection", (e) => {
  const err = e.reason as { message?: string; code?: string } | undefined;
  if (err?.code === "cancelled") return;
  useStore.getState().notify(`Erreur inattendue : ${err?.message ?? String(e.reason)}`, { level: "erreur" });
});

function App() {
  const [r, setR] = useState<Route>(route());
  useEffect(() => {
    const on = () => setR(route());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  // Retour des réglages : à la page d'où l'on vient (éditeur ou accueil), sinon à l'accueil.
  const back = () => { if (history.length > 1) history.back(); else location.hash = ""; };
  return (
    <>
      {r.page === "projet" ? (
        <Editor key={r.id} id={r.id} onBack={() => (location.hash = "")} />
      ) : r.page === "reglages" ? (
        <Settings onBack={back} />
      ) : (
        <Home onOpen={(id) => (location.hash = `#/p/${id}`)} />
      )}
      <Toasts />
      <DialogHost />
    </>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
