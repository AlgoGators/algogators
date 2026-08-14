import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    environment: 'node',
    coverage: {
      provider: 'v8',
      // Coverage is scoped to the logic layers. src/components (and the
      // App shell) are a Figma-exported UI tree, deliberately outside the
      // coverage contract until it is rebuilt component by component.
      include: [
        'src/domain/**/*.ts',
        'src/application/**/*.ts',
        'src/infrastructure/**/*.ts',
      ],
      exclude: [
        '**/*.test.ts',
        // Type-only modules: erased at compile time, nothing to execute.
        'src/domain/portfolio/portfolioData.ts',
        'src/domain/portfolio/incubationData.ts',
      ],
      thresholds: {
        lines: 80,
        functions: 80,
        branches: 80,
        statements: 80,
      },
    },
  },
});
