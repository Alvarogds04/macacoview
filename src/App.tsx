import "./App.css";
import { useState } from "react";
import { useAppState } from "./hooks/useAppState";
import { Recursos } from "./components/Recursos";
import { Tokens } from "./components/Tokens";
import { Puertos } from "./components/Puertos";
import { Procesos } from "./components/Procesos";
import { Settings } from "./components/Settings";

const TABS = [
  { key: "recursos", label: "Recursos" },
  { key: "tokens", label: "Tokens" },
  { key: "puertos", label: "Puertos" },
  { key: "procesos", label: "Procesos" },
  { key: "ajustes", label: "Ajustes" },
] as const;

function App() {
  const { snapshot, error, history } = useAppState();
  const [activeTab, setActiveTab] = useState("recursos");

  const handleTabClick = (key: (typeof TABS)[number]["key"]) => {
    setActiveTab(key);
  };

  return (
    <div className="app">
      <header className="header">
        <h1>🖥 PC-AI Monitor</h1>
      </header>

      <nav className="tabs" role="tablist">
        {TABS.map((tab) => (
          <span
            key={tab.key}
            className={`tab ${activeTab === tab.key ? "active" : ""}`}
            role="tab"
            aria-selected={activeTab === tab.key}
            onClick={() => handleTabClick(tab.key)}
          >
            {tab.label}
          </span>
        ))}
      </nav>

      <main className="tab-content">
        {activeTab === "recursos" && (
          <Recursos snapshot={snapshot} error={error} history={history} />
        )}

        {activeTab === "tokens" && (
          <Tokens snapshot={snapshot} error={error} />
        )}

        {activeTab === "puertos" && (
          <Puertos snapshot={snapshot} error={error} />
        )}

        {activeTab === "procesos" && (
          <Procesos snapshot={snapshot} error={error} />
        )}

        {activeTab === "ajustes" && <Settings />}
      </main>
    </div>
  );
}

export default App;
