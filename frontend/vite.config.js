import { defineConfig, loadEnv } from 'vite';

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), 'VITE_');
  return {
    define: {
      'import.meta.env.VITE_SERVER_URL': JSON.stringify(env.VITE_SERVER_URL)
    }
  };
});
