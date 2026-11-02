import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

// Unmount each screen so global listeners and query observers do not leak between tests.
afterEach(() => { cleanup(); localStorage.clear(); delete document.documentElement.dataset.largeType })
