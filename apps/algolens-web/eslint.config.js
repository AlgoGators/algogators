// Minimal flat config: correctness rules only. The imported code had no
// linter; start from recommended and tighten in-repo rather than blocking
// the migration on style.
import js from '@eslint/js';
import reactHooks from 'eslint-plugin-react-hooks';
import tseslint from 'typescript-eslint';

export default tseslint.config(
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    // The components carry `eslint-disable react-hooks/exhaustive-deps` comments, so the rule
    // must exist; it warns rather than fails until the Figma-exported tree is cleaned up.
    plugins: { 'react-hooks': reactHooks },
    rules: {
      'react-hooks/exhaustive-deps': 'warn',
      '@typescript-eslint/no-explicit-any': 'off',
      '@typescript-eslint/no-unused-vars': [
        'error',
        { argsIgnorePattern: '^_', varsIgnorePattern: '^_', ignoreRestSiblings: true },
      ],
    },
  }
);
