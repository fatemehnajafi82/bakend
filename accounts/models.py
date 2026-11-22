from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin, BaseUserManager
from django.db import models
from django.utils import timezone


class UserManager(BaseUserManager):
    def create_user(self, phone=None, **extra):
        user = self.model(phone=phone, **extra)
        user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_superuser(self, phone, password=None, **extra):
        extra.update(is_staff=True, is_superuser=True)
        user = self.model(phone=phone, **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user


class User(AbstractBaseUser, PermissionsMixin):
    phone = models.CharField(max_length=11, unique=True, null=True, blank=True)
    email = models.EmailField(unique=True, null=True, blank=True)
    name = models.CharField(max_length=100, blank=True)
    due_date = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)

    USERNAME_FIELD = "phone"
    objects = UserManager()

    def __str__(self):
        # کاربرانی که با ایمیل ثبت‌نام کرده‌اند phone ندارند (None)
        return self.phone or self.email or f"user-{self.pk}"

    def pregnancy_week(self, on=None):
        """هفتهٔ بارداری از روی تاریخ زایمان (۲۸۰ روز بارداری). بدون تاریخ یا خارج از بازه: None"""
        if not self.due_date:
            return None
        on = on or timezone.localdate()
        days = 280 - (self.due_date - on).days
        return days // 7 if 0 <= days <= 301 else None
