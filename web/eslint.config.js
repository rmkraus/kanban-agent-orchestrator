import js from "@eslint/js";
import globals from "globals";
import tseslint from "typescript-eslint";

export default [
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ["**/*.{ts,tsx,js,jsx}"],
    ignores: ["dist", "../src/kanban_agent_orchestrator/static"],
    languageOptions: {
      ecmaVersion: "latest",
      sourceType: "module",
      globals: {
        ...globals.browser,
      },
    },
    rules: {
      "max-len": ["warn", { code: 160, ignoreUrls: true, ignoreStrings: true, ignoreTemplateLiterals: true }],
    },
  },
];
