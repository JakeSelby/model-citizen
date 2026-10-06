import "@mantine/core/styles.css";
import "./styles.css";

import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { HashRouter } from "react-router-dom";

import { StudioApp } from "./StudioApp";
import { studioCssVariables, studioTheme } from "./theme";

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: false, staleTime: Infinity } },
});
const nonceElement = document.querySelector<HTMLMetaElement>('meta[name="studio-style-nonce"]');
const styleNonce = nonceElement?.content;
const getStyleNonce = styleNonce && !styleNonce.startsWith("__STUDIO_")
  ? () => styleNonce
  : undefined;

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <MantineProvider cssVariablesResolver={studioCssVariables} defaultColorScheme="auto" getStyleNonce={getStyleNonce} theme={studioTheme}>
      <QueryClientProvider client={queryClient}>
        <HashRouter>
          <StudioApp />
        </HashRouter>
      </QueryClientProvider>
    </MantineProvider>
  </StrictMode>,
);
