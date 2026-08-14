import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react-swc';
import path from 'path';

export default defineConfig({
  plugins: [react()],
  resolve: {
    extensions: ['.js', '.jsx', '.ts', '.tsx', '.json'],
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  build: {
    target: 'esnext',
    // dist/ is what the monorepo's node quality workflow measures for the
    // bundle budget and what the publish workflow ships.
    outDir: 'dist',
  },
  server: {
    port: 3000,
    open: true,
  },
});
