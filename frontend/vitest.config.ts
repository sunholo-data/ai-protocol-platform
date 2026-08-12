import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import path from 'path'

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    include: [
      'src/**/*.{test,spec}.{ts,tsx}',
      'src/**/__tests__/**/*.{ts,tsx}'
    ],
    exclude: [
      'node_modules',
      'dist',
      '.next',
      // Stale sub-agent worktrees from past sprint runs sit at
      // ../.claude/worktrees/<id>/frontend/src/** — vitest's include glob
      // matches their src/** and tries to load them, which fails because
      // they don't have node_modules installed.
      '**/.claude/worktrees/**',
    ],
    globals: true,
    passWithNoTests: true,
    css: true,
    reporters: process.env.CI ? ['basic'] : ['verbose'],
    pool: 'forks',
    poolOptions: {
      forks: {
        singleFork: process.env.CI === 'true'
      }
    },
    testTimeout: process.env.CI ? 30000 : 10000,
    coverage: {
      provider: 'v8',
      reporter: ['text', 'html', 'lcov', 'json'],
      exclude: [
        'node_modules/',
        'src/test/',
        '**/*.d.ts',
        '**/*.config.*',
        '**/coverage/**',
        'src/scripts/**',
      ]
    }
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
})
