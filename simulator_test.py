#!/usr/bin/python

import gflags
import croniter
import datetime
import simulator
import unittest

FLAGS = gflags.FLAGS


class TestSimulator(unittest.TestCase):

  def setUp(self):
    pass

  def testAccount(self):
    account = simulator.Account('Savings', 12345.67)
    account.timespec = croniter.croniter('0 0 1 * *')
    self.assertEqual(account.name, 'savings')
    self.assertEqual(account.value, 12345.67)

    account.rate = 0.10

    # Based on the monthly timespec how much do we grow each month at 10%?
    account.Preciate()
    self.assertEqual(account.value, 12392.990843258043)

    account.Preciate()
    self.assertEqual(account.value, 12440.493066887233)

  def testAmortization(self):
    mortgage = simulator.Account('Mortgage', -417000.0)
    mortgage.start_date = datetime.date(2013, 11, 1)
    mortgage.rate = 0.04
    mortgage.timespec = croniter.croniter('0 0 1 * *')
    mortgage.loan_months = 360
    mortgage.BuildAmortization()


  def testGeneralLedger(self):
    savings = simulator.Account('Savings', 12345.67)
    checking = simulator.Account('Checking', 9876.54)
    gl = simulator.GeneralLedger()
    gl.AddAccount(savings)
    gl.AddAccount(checking)

    self.assertEqual(gl.worth, 22222.21)

    mortgage = simulator.Account('Mortgage', -123456)
    credit_card = simulator.Account('Credit Card', -5000)
    gl.AddAccount(mortgage)
    gl.AddAccount(credit_card)

    self.assertEqual(gl.debt, -128456)
    self.assertEqual(gl.assets, 22222.21)

  def testLoadAccount(self):
    FLAGS.config = 'testdata/test_config.pb'
    gl = simulator.GeneralLedger()
    gl.LoadConfig()
    self.assertEqual(
        sorted(('checking', 'savings', 'house', 'mortgage')),
        sorted(gl.accounts.keys()))
    self.assertEqual(gl.inflation, 0.035)

  def testSweepRule(self):
    sr = simulator.SweepRule('savings', 5000.00)
    self.assertEqual(sr.sweep_account, 'savings')
    self.assertEqual(sr.amount, 5000)

  def testCashflow(self):
    start = '2013-01-01'
    end = '2015-01-01'
    cf = simulator.Cashflow(
        'credit card', 'checking', '0 0 1 * *', 2400, start,
        end, 'expense')
    self.assertEqual(cf.name, 'credit card')


if __name__ == '__main__':
  unittest.main()
